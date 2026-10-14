#!/usr/bin/env python3
"""Lädt pending 5-Minuten-Intervalle aus dem Cache in die enercast-Cloud."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from enercast_live import ENV_PATH, ApiError, LiveData, load_env
from interval_store import (
    PERIOD_MINUTES,
    IntervalStore,
    format_instant,
    parse_interval_start,
    utc_now,
)

DEFAULT_FLUSH_SECONDS = 45.0


def log(message: str) -> None:
    print(f"{format_instant(utc_now())} {message}", flush=True)


def plant_key_from_env() -> str:
    value = load_env(ENV_PATH).get("ENERCAST_PLANT_KEY", "")
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("ENERCAST_PLANT_KEY muss in der .env stehen.")
    return value.strip()


def flush_pending(client: LiveData, store: IntervalStore, plant_key: str) -> int:
    rows = store.list_upload_candidates()
    if not rows:
        return 0
    sent = 0
    for row in rows:
        start = parse_interval_start(row["interval_start"])
        try:
            client.send(
                plant_key,
                float(row["power_sent_kw"]),
                float(row["curtailment_w"]),
                power_unit="kW",
                start=start,
                period_minutes=PERIOD_MINUTES,
            )
        except (ApiError, ValueError, OSError) as exc:
            store.mark_failed(start, str(exc))
            log(f"Upload fehlgeschlagen für {row['interval_start']}: {exc}")
            continue
        store.mark_uploaded(start)
        sent += 1
        log(
            f"uploaded {row['interval_start']} "
            f"Leistung={row['power_sent_kw']} kW "
            f"Abregelung={row['curtailment_w']:.0f} W"
        )
    return sent


def run_once(store: IntervalStore) -> int:
    try:
        client = LiveData.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    plant_key = plant_key_from_env()
    return flush_pending(client, store, plant_key)


def run_daemon(store: IntervalStore, flush_seconds: float) -> None:
    log(f"post start, flush alle {flush_seconds:g} s")
    while True:
        try:
            count = run_once(store)
            if count:
                log(f"{count} Intervall(e) gesendet")
        except SystemExit as exc:
            log(f"Konfiguration: {exc}")
        except Exception as exc:
            log(f"flush-Fehler: {exc}")
        time.sleep(flush_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache-Intervalle an enercast senden.")
    parser.add_argument(
        "--db",
        type=Path,
        help="SQLite-Pfad (Default: ENERCAST_INTERVAL_DB oder data/modbus_intervals.db)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ein Durchlauf, dann beenden",
    )
    parser.add_argument(
        "--flush-seconds",
        type=float,
        default=DEFAULT_FLUSH_SECONDS,
        help=f"Pause zwischen Durchläufen im Dämon-Modus (Default {DEFAULT_FLUSH_SECONDS:g})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = IntervalStore(args.db)
    try:
        if args.once:
            count = run_once(store)
            log(f"fertig, {count} Intervall(e) gesendet")
            return
        run_daemon(store, args.flush_seconds)
    except KeyboardInterrupt:
        log("beendet")
    finally:
        store.close()


if __name__ == "__main__":
    main()
