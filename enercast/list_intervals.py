#!/usr/bin/env python3
"""Listet die bis zu 16 gespeicherten Modbus-15-Minuten-Intervalle aus dem Cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from interval_store import (
    KEEP_INTERVALS,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_UPLOADED,
    IntervalStore,
    default_db_path,
)

ALL_STATUSES = (STATUS_PENDING, STATUS_UPLOADED, STATUS_FAILED, STATUS_SKIPPED)


def format_row(row) -> str:
    err = row["last_error"]
    err_part = f"  error={err!r}" if err else ""
    return (
        f"{row['interval_start']}  {row['status']:8}  "
        f"mean={row['power_mean_kw']:8.2f} kW  send={row['power_sent_kw']:8.2f} kW  "
        f"curt={row['curtailment_w']:9.0f} W  "
        f"samples P/L={row['power_samples']}/{row['limit_samples']}"
        f"{err_part}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Intervall-Cache (max. {KEEP_INTERVALS} Fenster, älteste werden beim Schreiben gelöscht)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        help="SQLite-Pfad (Default: ENERCAST_INTERVAL_DB oder data/modbus_intervals.db)",
    )
    parser.add_argument(
        "--status",
        choices=ALL_STATUSES,
        help="Nur diesen Status anzeigen (sonst alle bis zu 16)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = args.db or default_db_path()
    store = IntervalStore(db)
    try:
        counts = store.count_by_status()
        total = sum(counts.values())
        print(f"DB: {store.db_path}  (max. {KEEP_INTERVALS} Intervalle)")
        print(f"Zeilen: {total}")
        for status in ALL_STATUSES:
            n = counts.get(status, 0)
            if n:
                print(f"  {status}: {n}")
        rows = store.list_all(status=args.status, newest_first=False)
        if not rows:
            print("(leer)")
            return 0
        print("---")
        for row in rows:
            print(format_row(row))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
