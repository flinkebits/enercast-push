"""Fenster und Ertragsdifferenz ohne Netz."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fusionsolar_history import (
    BERLIN,
    DAY_MS,
    STEP_MS,
    WINDOW_MS,
    floor_5min_ms,
    format_watt,
    interval_labels,
    parse_instant_ms,
    watt_rows,
    window_end_ms,
)


class WindowTest(unittest.TestCase):
    def test_window_stays_under_one_day(self):
        start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end = window_end_ms(start, start + 10 * DAY_MS)
        self.assertLess(end - start, DAY_MS)
        self.assertEqual(end - start, WINDOW_MS)

    def test_window_stops_at_final_end(self):
        final = int(datetime(2026, 10, 17, tzinfo=BERLIN).timestamp() * 1000)
        start = final - 60 * 60 * 1000
        self.assertEqual(window_end_ms(start, final), final)


class YieldTest(unittest.TestCase):
    def test_five_minute_yield_becomes_watts(self):
        start = int(datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN).timestamp() * 1000)
        rows, last = watt_rows(
            [(start, 100.0), (start + STEP_MS, 100.5)],
            None,
            start,
            start + DAY_MS,
        )
        self.assertEqual(rows, [{"Start": "2026-10-16 12:00", "Ende": "2026-10-16 12:05", "Watt": "6000"}])
        self.assertEqual(last, (start + STEP_MS, 100.5))

    def test_previous_at_window_start_pairs_with_the_next_sample(self):
        start = int(datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN).timestamp() * 1000)
        rows, _last = watt_rows(
            [(start + STEP_MS, 100.25)],
            (start, 100.0),
            start,
            start + 2 * STEP_MS,
        )
        self.assertEqual(rows[0]["Start"], "2026-10-16 12:00")
        self.assertEqual(rows[0]["Watt"], "3000")

    def test_sample_before_the_window_is_not_repeated(self):
        start = int(datetime(2026, 10, 16, 12, 5, tzinfo=BERLIN).timestamp() * 1000)
        rows, last = watt_rows([(start, 100.25)], (start - STEP_MS, 100.0), start, start + STEP_MS)
        self.assertEqual(rows, [])
        self.assertEqual(last, (start, 100.25))

    def test_falling_counter_and_gap_make_no_row(self):
        start = int(datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN).timestamp() * 1000)
        rows, last = watt_rows(
            [(start, 10.0), (start + STEP_MS, 9.0), (start + 3 * STEP_MS, 9.5)],
            None,
            start,
            start + DAY_MS,
        )
        self.assertEqual(rows, [])
        self.assertEqual(last[0], start + 3 * STEP_MS)

    def test_last_interval_of_16_october(self):
        start = int(datetime(2026, 10, 16, 23, 55, tzinfo=BERLIN).timestamp() * 1000)
        label_start, label_end = interval_labels(start)
        self.assertEqual(label_start, "2026-10-16 23:55")
        self.assertEqual(label_end, "2026-10-17 00:00")

    def test_grid_connection_floors_to_five_minutes(self):
        ms = parse_instant_ms("2024-06-01T10:07:30+02:00")
        self.assertEqual(floor_5min_ms(ms), ms)
        moment = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(BERLIN)
        self.assertEqual(moment.minute % 5, 0)

    def test_watt_format_uses_dot(self):
        self.assertEqual(format_watt(1.5), "1.5")
        self.assertEqual(format_watt(0.0), "0")
