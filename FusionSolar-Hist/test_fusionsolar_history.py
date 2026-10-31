"""Fenster und Ertragsdifferenz ohne Netz."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fusionsolar_history import (
    BERLIN,
    CSV_PATH,
    DAY_MS,
    STATE_PATH,
    STEP_MS,
    WINDOW_MS,
    complete_quarters,
    day_end_ms,
    floor_5min_ms,
    format_power_row,
    format_watt,
    interval_labels,
    output_paths,
    parse_args,
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
        self.assertEqual(rows, [{"Start": "2026-10-16 12:00:00", "Ende": "2026-10-16 12:05:00", "Watt": "6000"}])
        self.assertEqual(last, (start + STEP_MS, 100.5))

    def test_previous_at_window_start_pairs_with_the_next_sample(self):
        start = int(datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN).timestamp() * 1000)
        rows, _last = watt_rows(
            [(start + STEP_MS, 100.25)],
            (start, 100.0),
            start,
            start + 2 * STEP_MS,
        )
        self.assertEqual(rows[0]["Start"], "2026-10-16 12:00:00")
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
        self.assertEqual(label_start, "2026-10-16 23:55:00")
        self.assertEqual(label_end, "2026-10-17 00:00:00")

    def test_grid_connection_floors_to_five_minutes(self):
        ms = parse_instant_ms("2024-06-01T10:07:30+02:00")
        self.assertEqual(floor_5min_ms(ms), ms)
        moment = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(BERLIN)
        self.assertEqual(moment.minute % 5, 0)

    def test_watt_format_uses_dot(self):
        self.assertEqual(format_watt(1.5), "1.5")
        self.assertEqual(format_watt(0.0), "0")


class DayTest(unittest.TestCase):
    def test_day_end_is_midnight_of_the_next_day(self):
        end = day_end_ms("2026-03-08")
        expected = int(datetime(2026, 3, 9, tzinfo=BERLIN).timestamp() * 1000)
        self.assertEqual(end, expected)
        self.assertEqual(end - parse_instant_ms("2026-03-08"), DAY_MS)

    def test_day_end_across_dst_change_counts_wall_clock(self):
        start = int(datetime(2026, 10, 25, tzinfo=BERLIN).timestamp() * 1000)
        end = int(datetime(2026, 10, 26, tzinfo=BERLIN).timestamp() * 1000)
        self.assertEqual(day_end_ms("2026-10-25"), end)
        self.assertEqual(end - start, 25 * 3600 * 1000)


class ArgsTest(unittest.TestCase):
    def test_dates_are_optional(self):
        args = parse_args([])
        self.assertIsNone(args.start)
        self.assertIsNone(args.end)
        self.assertEqual(args.interval, 5)

    def test_dates_are_parsed(self):
        args = parse_args(["--start", "2026-01-01", "--end", "2026-01-31"])
        self.assertEqual(args.start, "2026-01-01")
        self.assertEqual(args.end, "2026-01-31")

    def test_interval_accepts_fifteen_minutes(self):
        args = parse_args(["--interval", "15"])
        self.assertEqual(args.interval, 15)
        csv_file, state_file = output_paths(args.interval)
        self.assertEqual(csv_file.name, "leistung-15min.csv")
        self.assertEqual(state_file.name, "state-15min.json")

    def test_five_minutes_keeps_the_original_files(self):
        self.assertEqual(output_paths(5), (CSV_PATH, STATE_PATH))

    def test_other_intervals_are_rejected(self):
        with self.assertRaises(SystemExit):
            parse_args(["--interval", "10"])


class QuarterTest(unittest.TestCase):
    def test_three_steps_become_one_mean(self):
        start = int(datetime(2026, 10, 30, 8, 0, tzinfo=BERLIN).timestamp() * 1000)
        rows = [(start, 14160.0), (start + STEP_MS, 24360.0), (start + 2 * STEP_MS, 39360.0)]
        done, pending = complete_quarters([], rows)
        self.assertEqual(done, [(start, 25960.0)])
        self.assertEqual(pending, [])
        self.assertEqual(
            format_power_row(done[0][0], done[0][1], 15),
            {"Start": "2026-10-30 08:00:00", "Ende": "2026-10-30 08:15:00", "Watt": "25960"},
        )

    def test_open_quarter_is_kept_until_the_third_step(self):
        start = int(datetime(2026, 10, 30, 8, 0, tzinfo=BERLIN).timestamp() * 1000)
        done, pending = complete_quarters([], [(start, 14160.0), (start + STEP_MS, 24360.0)])
        self.assertEqual(done, [])
        self.assertEqual(len(pending), 2)
        done, pending = complete_quarters(pending, [(start + 2 * STEP_MS, 39360.0)])
        self.assertEqual(done, [(start, 25960.0)])
        self.assertEqual(pending, [])

    def test_gap_drops_the_incomplete_quarter(self):
        start = int(datetime(2026, 10, 30, 8, 0, tzinfo=BERLIN).timestamp() * 1000)
        later = start + 15 * 60 * 1000
        rows = [
            (start, 1000.0),
            (start + STEP_MS, 2000.0),
            (later, 3000.0),
            (later + STEP_MS, 3000.0),
            (later + 2 * STEP_MS, 3000.0),
        ]
        done, pending = complete_quarters([], rows)
        self.assertEqual(done, [(later, 3000.0)])
        self.assertEqual(pending, [])

    def test_repeated_dst_hour_stays_two_quarters(self):
        first = datetime(2026, 10, 25, 2, 0, tzinfo=BERLIN)
        second = datetime(2026, 10, 25, 2, 0, fold=1, tzinfo=BERLIN)
        self.assertNotEqual(int(first.timestamp()), int(second.timestamp()))

        def triple(moment: datetime, watts: float) -> list[tuple[int, float]]:
            start = int(moment.timestamp() * 1000)
            return [(start, watts), (start + STEP_MS, watts), (start + 2 * STEP_MS, watts)]

        done, pending = complete_quarters([], triple(first, 0.0) + triple(second, 12000.0))
        self.assertEqual(pending, [])
        self.assertEqual([watts for _start, watts in done], [0.0, 12000.0])
        self.assertEqual(interval_labels(done[0][0], 15)[0], "2026-10-25 02:00:00")
        self.assertEqual(interval_labels(done[1][0], 15)[0], "2026-10-25 02:00:00")
