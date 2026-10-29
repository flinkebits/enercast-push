"""Tests für list_intervals / Retention."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from interval_store import KEEP_INTERVALS, STATUS_PENDING, IntervalStore


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IntervalStore(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_prune_keeps_newest_sixteen(self):
        base = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        for i in range(KEEP_INTERVALS + 3):
            start = base + timedelta(minutes=5 * i)
            self.store.save(
                start,
                power_mean_kw=float(i),
                power_sent_kw=float(i),
                curtailment_w=0.0,
                power_samples=25,
                limit_samples=25,
                status=STATUS_PENDING,
            )
        rows = self.store.list_all()
        self.assertEqual(len(rows), KEEP_INTERVALS)
        self.assertEqual(rows[0]["interval_start"], "2026-10-04T10:15:00Z")
        self.assertEqual(rows[-1]["interval_start"], "2026-10-04T11:30:00Z")


if __name__ == "__main__":
    unittest.main()
