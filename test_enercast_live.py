"""Prüfungen und Intervallberechnung ohne Netz."""

import unittest
from datetime import datetime, timezone

from enercast_live import (
    LiveData,
    ceiling_watts,
    parse_start,
    require_within,
    to_watts,
)


class ValidationTest(unittest.TestCase):
    def test_negative_power_rejected(self):
        with self.assertRaises(ValueError):
            require_within("Leistung", -1, None)

    def test_power_above_rated_power_rejected(self):
        ceiling = ceiling_watts({"rated-power": 1000, "net-active-power": 800})
        self.assertEqual(ceiling, 1050)
        with self.assertRaises(ValueError):
            require_within("Leistung", 1051, ceiling)

    def test_power_within_tolerance_accepted(self):
        ceiling = ceiling_watts({"rated-power": 1000})
        require_within("Leistung", 1050, ceiling)

    def test_negative_curtailment_rejected(self):
        with self.assertRaises(ValueError):
            require_within("Abregelung", -0.1, 1000)

    def test_curtailment_above_plant_power_rejected(self):
        ceiling = ceiling_watts({"net-active-power": 2000})
        with self.assertRaises(ValueError):
            require_within("Abregelung", 2101, ceiling)

    def test_missing_limits_only_check_non_negative(self):
        self.assertIsNone(ceiling_watts({}))
        require_within("Leistung", 10_000_000, None)

    def test_kilowatt_conversion(self):
        self.assertEqual(to_watts(1.2, "kW"), 1200)
        self.assertEqual(to_watts(1234.5, "W"), 1234.5)

    def test_non_finite_power_rejected(self):
        with self.assertRaises(ValueError):
            to_watts(float("nan"), "W")

    def test_timestamp_not_on_minute_rejected(self):
        now = datetime(2026, 10, 4, 8, 46, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            parse_start("2026-10-04T08:46:30Z", now)

    def test_non_utc_timestamp_rejected(self):
        now = datetime(2026, 10, 4, 8, 46, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            parse_start("2026-10-04T10:46:00+02:00", now)

    def test_default_interval_is_previous_minute(self):
        now = datetime(2026, 10, 4, 8, 46, 30, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start(None, now),
            datetime(2026, 10, 4, 8, 45, tzinfo=timezone.utc),
        )

    def test_default_quarter_is_previous_completed_slice(self):
        now = datetime(2026, 10, 4, 10, 17, 30, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start(None, now, 15),
            datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc),
        )

    def test_default_quarter_on_boundary_is_previous_slice(self):
        now = datetime(2026, 10, 4, 10, 15, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start(None, now, 15),
            datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc),
        )

    def test_default_quarter_crosses_hour(self):
        now = datetime(2026, 10, 4, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start(None, now, 15),
            datetime(2026, 10, 3, 23, 45, tzinfo=timezone.utc),
        )

    def test_default_five_minute_is_previous_slice(self):
        now = datetime(2026, 10, 4, 10, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start(None, now, 5),
            datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc),
        )

    def test_five_minute_start_accepted(self):
        now = datetime(2026, 10, 4, 10, 12, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start("2026-10-04T10:05:00Z", now, 5),
            datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc),
        )

    def test_quarter_hour_start_accepted(self):
        now = datetime(2026, 10, 4, 10, 17, tzinfo=timezone.utc)
        self.assertEqual(
            parse_start("2026-10-04T10:15:00Z", now, 15),
            datetime(2026, 10, 4, 10, 15, tzinfo=timezone.utc),
        )

    def test_off_grid_quarter_rejected(self):
        now = datetime(2026, 10, 4, 10, 17, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            parse_start("2026-10-04T10:07:00Z", now, 15)

    def test_period_must_divide_hour(self):
        now = datetime(2026, 10, 4, 10, 17, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            parse_start(None, now, 7)

    def test_quarter_payload_uses_fifteen_minute_slice(self):
        client = LiveData("user", "secret")
        captured = []

        def capture(method, path, token=None, body=None, query=None):
            captured.append(body)

        client._request = capture
        start = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        client._post_power("token", "Feld0", 1000.0, start, 15)
        client._post_curtailment("token", "Feld0", 500.0, start, 15)
        self.assertEqual(captured[0]["periodicity"], ["R/PT15M"])
        self.assertEqual(captured[0]["start"], "2026-10-04T10:00:00Z")
        self.assertEqual(captured[0]["values"], [1000.0])
        self.assertEqual(captured[1]["start-instant"], "2026-10-04T10:00:00Z")
        self.assertEqual(captured[1]["end-instant"], "2026-10-04T10:15:00Z")

    def test_default_payload_stays_one_minute(self):
        client = LiveData("user", "secret")
        captured = []

        def capture(method, path, token=None, body=None, query=None):
            captured.append(body)

        client._request = capture
        start = datetime(2026, 10, 4, 8, 45, tzinfo=timezone.utc)
        client._post_power("token", "Feld0", 1000.0, start, 1)
        client._post_curtailment("token", "Feld0", 500.0, start, 1)
        self.assertEqual(captured[0]["periodicity"], ["R/PT1M"])
        self.assertEqual(captured[1]["end-instant"], "2026-10-04T08:46:00Z")

    def test_invalid_values_do_not_login(self):
        client = LiveData("user", "secret")

        def fail_login():
            raise AssertionError("Login darf nicht laufen.")

        client._login = fail_login
        with self.assertRaises(ValueError):
            client.send("Feld0", power=-1, curtailment_w=0)
        with self.assertRaises(ValueError):
            client.send("Feld0", power=1, curtailment_w=0, period_minutes=7)
        with self.assertRaises(ValueError):
            client.send(
                "Feld0",
                power=1,
                curtailment_w=0,
                start="2026-10-04T10:07:00Z",
                period_minutes=15,
            )


if __name__ == "__main__":
    unittest.main()
