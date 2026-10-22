#!/usr/bin/env python3
"""5-Minuten-CSV auf 15-Minuten-Mittelwerte eindampfen.

Startzeiten liegen auf Minute 0, 15, 30 oder 45. Die Ausgabedatei liegt
neben der Quelle, Feldtrenner ist das Semikolon.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from fusionsolar_history import format_watt

COLUMNS = ("Start", "Ende", "Watt")
STAMP = "%Y-%m-%d %H:%M"


def quarter_start(moment: datetime) -> datetime:
    minute = (moment.minute // 15) * 15
    return moment.replace(minute=minute, second=0, microsecond=0)


def mean_quarters(rows: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for start, watt in rows:
        buckets[quarter_start(start)].append(watt)
    return [(start, sum(values) / len(values)) for start, values in sorted(buckets.items())]


def read_rows(path: Path) -> list[tuple[datetime, float]]:
    rows = []
    with path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            text = (record.get("Watt") or "").strip()
            if not text:
                continue
            rows.append((datetime.strptime(record["Start"], STAMP), float(text)))
    return rows


def write_rows(path: Path, quarters: list[tuple[datetime, float]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(COLUMNS)
        for start, watt in quarters:
            end = start + timedelta(minutes=15)
            writer.writerow((start.strftime(STAMP), end.strftime(STAMP), format_watt(watt)))


def output_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}-15min{source.suffix}")


def convert(source: Path) -> Path:
    target = output_path(source)
    write_rows(target, mean_quarters(read_rows(source)))
    return target


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "data" / "leistung.csv"
    target = convert(source)
    print(target)


if __name__ == "__main__":
    main()
