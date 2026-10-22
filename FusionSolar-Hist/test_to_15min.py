"""15-Minuten-Mittel aus 5-Minuten-Zeilen."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from to_15min import convert, mean_quarters, output_path, quarter_start


class QuarterTest(unittest.TestCase):
    def test_floors_to_quarter_hour(self):
        self.assertEqual(quarter_start(datetime(2026, 5, 29, 0, 10)).minute, 0)
        self.assertEqual(quarter_start(datetime(2026, 5, 29, 0, 15)).minute, 15)
        self.assertEqual(quarter_start(datetime(2026, 5, 29, 0, 45)).minute, 45)

    def test_mean_of_three_five_minute_values(self):
        start = datetime(2026, 5, 29, 12, 0)
        rows = [
            (start, 100.0),
            (start.replace(minute=5), 200.0),
            (start.replace(minute=10), 300.0),
            (start.replace(minute=15), 900.0),
        ]
        quarters = mean_quarters(rows)
        self.assertEqual(quarters[0], (start, 200.0))
        self.assertEqual(quarters[1][0].minute, 15)
        self.assertEqual(quarters[1][1], 900.0)

    def test_writes_semicolon_file_beside_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "leistung.csv"
            source.write_text(
                "Start,Ende,Watt\n"
                "2026-05-29 00:00,2026-05-29 00:05,3\n"
                "2026-05-29 00:05,2026-05-29 00:10,6\n"
                "2026-05-29 00:10,2026-05-29 00:15,9\n"
            )
            target = convert(source)
            self.assertEqual(target, output_path(source))
            self.assertEqual(target.parent, source.parent)
            text = target.read_text()
            self.assertTrue(text.startswith("Start;Ende;Watt\n"))
            self.assertIn("2026-05-29 00:00;2026-05-29 00:15;6", text)
