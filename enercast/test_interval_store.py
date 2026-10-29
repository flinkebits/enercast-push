"""Tests für interval_store ohne Modbus."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from interval_store import (
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_UPLOADED,
    IntervalStore,
    floor_interval,
    parse_interval_start,
)


class FloorIntervalTest(unittest.TestCase):
    def test_floors_to_five_minute_grid(self):
        moment = datetime(2026, 10, 4, 10, 7, 23, tzinfo=timezone.utc)
        self.assertEqual(
            floor_interval(moment, 5),
            datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc),
        )

    def test_on_boundary_unchanged(self):
        moment = datetime(2026, 10, 4, 10, 15, tzinfo=timezone.utc)
        self.assertEqual(
            floor_interval(moment, 5),
            datetime(2026, 10, 4, 10, 15, tzinfo=timezone.utc),
        )


class IntervalStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.store = IntervalStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_pending_then_uploaded_not_overwritten(self):
        start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        self.store.save(
            start,
            power_mean_kw=1.0,
            power_sent_kw=1.0,
            curtailment_w=100.0,
            power_samples=200,
            limit_samples=200,
            status=STATUS_PENDING,
        )
        self.store.mark_uploaded(start)
        saved = self.store.save(
            start,
            power_mean_kw=9.0,
            power_sent_kw=9.0,
            curtailment_w=900.0,
            power_samples=200,
            limit_samples=200,
            status=STATUS_PENDING,
        )
        self.assertFalse(saved)
        row = self.store.get(start)
        assert row is not None
        self.assertEqual(row["status"], STATUS_UPLOADED)
        self.assertEqual(row["power_sent_kw"], 1.0)

    def test_list_upload_candidates_order(self):
        a = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        b = datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc)
        for start, power in ((b, 2.0), (a, 1.0)):
            self.store.save(
                start,
                power_mean_kw=power,
                power_sent_kw=power,
                curtailment_w=0.0,
                power_samples=200,
                limit_samples=200,
                status=STATUS_PENDING,
            )
        rows = self.store.list_upload_candidates()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["interval_start"], "2026-10-04T10:00:00Z")

    def test_parse_interval_start(self):
        parsed = parse_interval_start("2026-10-04T10:05:00Z")
        self.assertEqual(parsed.minute, 5)
        self.assertEqual(parsed.second, 0)

    def test_skipped_row_stored(self):
        start = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)
        self.store.save(
            start,
            power_mean_kw=0.0,
            power_sent_kw=0.0,
            curtailment_w=0.0,
            power_samples=1,
            limit_samples=1,
            status=STATUS_SKIPPED,
            last_error="zu wenige Proben",
        )
        rows = self.store.list_upload_candidates()
        self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
