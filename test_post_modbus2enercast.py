"""Tests für Upload-Schleife mit Mock-Client."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from enercast_live import LiveData
from interval_store import STATUS_PENDING, STATUS_UPLOADED, IntervalStore
from post_modbus2enercast import flush_pending


class SendCall:
    def __init__(self, plant_key, power, curtailment_w, **kwargs):
        self.plant_key = plant_key
        self.power = power
        self.curtailment_w = curtailment_w
        self.kwargs = kwargs


class FlushPendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.store = IntervalStore(self.db)
        self.window = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        self.sends = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def save(self, minute, power=1.5, curtailment=200.0):
        start = self.window.replace(minute=minute)
        self.store.save(
            start,
            power_mean_kw=power,
            power_sent_kw=power,
            curtailment_w=curtailment,
            power_samples=250,
            limit_samples=250,
            status=STATUS_PENDING,
        )
        return start

    def make_client(self):
        client = LiveData("user", "secret")

        def capture(*args, **kwargs):
            self.sends.append(SendCall(*args, **kwargs))
            return self.window

        client.send = capture  # type: ignore[method-assign]
        return client

    def test_full_window_sent_as_15min_average(self):
        for minute in (0, 5, 10):
            self.save(minute, power=1.0 + minute / 5)
        now = self.window + timedelta(minutes=16)
        count = flush_pending(self.make_client(), self.store, "Feld0", now=now)
        self.assertEqual(count, 3)
        self.assertEqual(len(self.sends), 1)
        send = self.sends[0]
        self.assertEqual(send.plant_key, "Feld0")
        self.assertEqual(send.power, 2.0)  # Mittel aus 1, 2, 3 kW
        self.assertEqual(send.kwargs["period_minutes"], 15)
        self.assertEqual(send.kwargs["start"], self.window)
        for minute in (0, 5, 10):
            row = self.store.get(self.window.replace(minute=minute))
            assert row is not None
            self.assertEqual(row["status"], STATUS_UPLOADED)

    def test_incomplete_window_waits_until_final(self):
        self.save(0)
        self.save(5)
        now = self.window + timedelta(minutes=12)
        count = flush_pending(self.make_client(), self.store, "Feld0", now=now)
        self.assertEqual(count, 0)
        self.assertEqual(self.sends, [])
        row = self.store.get(self.window)
        assert row is not None
        self.assertEqual(row["status"], STATUS_PENDING)

    def test_stub_of_two_sent_as_10min_average(self):
        self.save(5, power=2.0, curtailment=100.0)
        self.save(10, power=4.0, curtailment=300.0)
        now = self.window + timedelta(minutes=18)
        count = flush_pending(self.make_client(), self.store, "Feld0", now=now)
        self.assertEqual(count, 2)
        self.assertEqual(len(self.sends), 1)
        send = self.sends[0]
        self.assertEqual(send.power, 3.0)
        self.assertEqual(send.curtailment_w, 200.0)
        self.assertEqual(send.kwargs["period_minutes"], 10)
        self.assertEqual(send.kwargs["start"], self.window.replace(minute=5))

    def test_stub_of_one_sent_as_5min(self):
        self.save(10)
        now = self.window + timedelta(minutes=18)
        count = flush_pending(self.make_client(), self.store, "Feld0", now=now)
        self.assertEqual(count, 1)
        send = self.sends[0]
        self.assertEqual(send.kwargs["period_minutes"], 5)
        self.assertEqual(send.kwargs["start"], self.window.replace(minute=10))

    def test_mixed_windows_flushed_independently(self):
        # Volles Fenster 10:00-10:15 und Stummel (nur 10:15) im nächsten Fenster.
        for minute in (0, 5, 10):
            self.save(minute)
        self.save(15)
        now = self.window + timedelta(minutes=34)
        count = flush_pending(self.make_client(), self.store, "Feld0", now=now)
        self.assertEqual(count, 4)
        self.assertEqual(len(self.sends), 2)
        periods = sorted(send.kwargs["period_minutes"] for send in self.sends)
        self.assertEqual(periods, [5, 15])

    @patch.dict("os.environ", {}, clear=False)
    def test_run_once_uses_env_plant_key(self):
        from post_modbus2enercast import run_once

        for minute in (0, 5, 10):
            self.save(minute)
        client = LiveData("user", "secret")
        seen = {}

        def capture_send(plant_key, power, curtailment_w, **kwargs):
            seen["plant_key"] = plant_key
            seen["kwargs"] = kwargs
            return self.window

        with patch("post_modbus2enercast.LiveData.from_env", return_value=client):
            with patch("post_modbus2enercast.plant_key_from_env", return_value="Feld0"):
                client.send = capture_send  # type: ignore[method-assign]
                count = run_once(self.store)
        self.assertEqual(count, 3)
        self.assertEqual(seen["plant_key"], "Feld0")
        self.assertEqual(seen["kwargs"]["period_minutes"], 15)
        self.assertEqual(seen["kwargs"]["start"], self.window)


if __name__ == "__main__":
    unittest.main()
