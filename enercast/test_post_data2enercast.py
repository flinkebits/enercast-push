"""Tests für Upload-Schleife mit Mock-Client."""

import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from libenercast import LiveData
from interval_store import STATUS_PENDING, IntervalStore
from post_data2enercast import flush_pending


class FlushPendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.store = IntervalStore(self.db)
        self.start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        self.store.save(
            self.start,
            power_mean_kw=1.5,
            power_sent_kw=1.5,
            curtailment_w=200.0,
            power_samples=250,
            limit_samples=250,
            status=STATUS_PENDING,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_flush_marks_uploaded(self):
        client = LiveData("user", "secret")
        client.send = lambda *args, **kwargs: self.start  # type: ignore[method-assign]
        buf = StringIO()
        with redirect_stdout(buf):
            count = flush_pending(client, self.store, "Feld0")
        self.assertEqual(count, 1)
        self.assertIn(
            "uploaded 2026-10-04T10:00:00Z - 10:15 Leistung=1.5 kW Abregelung=0.200 kW",
            buf.getvalue(),
        )
        row = self.store.get(self.start)
        assert row is not None
        self.assertEqual(row["status"], "uploaded")

    @patch.dict("os.environ", {}, clear=False)
    def test_run_once_uses_env_plant_key(self):
        from post_data2enercast import run_once

        client = LiveData("user", "secret")
        seen = {}

        def capture_send(plant_key, power, curtailment_w, **kwargs):
            seen["plant_key"] = plant_key
            seen["kwargs"] = kwargs
            return self.start

        with patch("post_data2enercast.LiveData.from_env", return_value=client):
            with patch("post_data2enercast.plant_key_from_env", return_value="Feld0"):
                client.send = capture_send  # type: ignore[method-assign]
                count = run_once(self.store)
        self.assertEqual(count, 1)
        self.assertEqual(seen["plant_key"], "Feld0")
        self.assertEqual(seen["kwargs"]["period_minutes"], 15)

    def test_off_grid_row_skipped_not_sent(self):
        off = datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc)
        self.store.save(
            off,
            power_mean_kw=1.0,
            power_sent_kw=1.0,
            curtailment_w=0.0,
            power_samples=20,
            limit_samples=20,
            status=STATUS_PENDING,
        )
        sent = []

        def capture_send(*_args, **kwargs):
            sent.append(kwargs.get("start"))
            return self.start

        client = LiveData("user", "secret")
        client.send = capture_send  # type: ignore[method-assign]
        count = flush_pending(client, self.store, "Feld0")
        self.assertEqual(count, 1)
        self.assertEqual(sent, [self.start])
        row = self.store.get(off)
        assert row is not None
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["last_error"], "Raster nicht 15 min")


if __name__ == "__main__":
    unittest.main()
