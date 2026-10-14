"""Tests für Bucket-Auswertung ohne Modbus."""

import unittest
from datetime import datetime, timezone

from collect_modbus2enercast import EXPECTED_SAMPLES, MIN_SAMPLES, Bucket, evaluate_bucket
from interval_store import STATUS_PENDING, STATUS_SKIPPED


class EvaluateBucketTest(unittest.TestCase):
    def test_too_few_samples_skipped(self):
        start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        bucket = Bucket(start, powers=[1.0] * (MIN_SAMPLES - 1), limits=[0.0] * MIN_SAMPLES)
        status, *_rest, reason = evaluate_bucket(bucket)
        self.assertEqual(status, STATUS_SKIPPED)
        self.assertIn("zu wenige Proben", reason or "")

    def test_valid_bucket_pending(self):
        start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        powers = [2.0] * MIN_SAMPLES
        limits = [0.5] * MIN_SAMPLES
        bucket = Bucket(start, powers=powers, limits=limits)
        status, power_mean, power_sent, curtailment_w, _limit, reason = evaluate_bucket(bucket)
        self.assertEqual(status, STATUS_PENDING)
        self.assertIsNone(reason)
        self.assertEqual(power_mean, 2.0)
        self.assertEqual(power_sent, 2.0)
        self.assertEqual(curtailment_w, 500.0)

    def test_negative_power_sent_as_zero(self):
        start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        bucket = Bucket(start, powers=[-1.0] * MIN_SAMPLES, limits=[0.0] * MIN_SAMPLES)
        status, power_mean, power_sent, _curt, _lim, reason = evaluate_bucket(bucket)
        self.assertEqual(status, STATUS_PENDING)
        self.assertEqual(power_mean, -1.0)
        self.assertEqual(power_sent, 0.0)
        self.assertIsNone(reason)

    def test_expected_samples_constant(self):
        self.assertEqual(EXPECTED_SAMPLES, 100)
        self.assertEqual(MIN_SAMPLES, 25)


if __name__ == "__main__":
    unittest.main()
