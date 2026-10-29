#!/usr/bin/env python3
"""Lädt pending 5-Minuten-Intervalle aus dem Cache als 15-Minuten-Werte in die enercast-Cloud.

Je 3 aufeinanderfolgende 5-Minuten-Intervalle werden zu einem 15-Minuten-Mittelwert
am Raster :00/:15/:30/:45 zusammengefasst. Enthält der Cache zu einem Fenster nur
1-2 Intervalle (Stummel, z. B. nach Daemon-Neustart), wird der Mittelwert der
vorhandenen Intervalle als 5- bzw. 10-Minuten-Wert gesendet — erst nachdem das
Fenster abgeschlossen ist, damit kein vorzeitig unvollständiges Fenster hochgeht.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
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
ENERCAST_PERIOD_MINUTES = 15
GROUP_SIZE = ENERCAST_PERIOD_MINUTES // PERIOD_MINUTES
# Der letzte 5-Minuten-Bucket eines Fensters wird von collect erst nach
# Fensterende geschrieben; kurze Wartezeit, damit kein Stummel vorzeitig geht.
WINDOW_GRACE_SECONDS = 120.0


def log(message: str) -> None:
    print(f"{format_instant(utc_now())} {message}", flush=True)


def plant_key_from_env() -> str:
    value = load_env(ENV_PATH).get("ENERCAST_PLANT_KEY", "")
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("ENERCAST_PLANT_KEY muss in der .env stehen.")
    return value.strip()


def window_start_of(start: datetime) -> datetime:
    minute = (start.minute // ENERCAST_PERIOD_MINUTES) * ENERCAST_PERIOD_MINUTES
    return start.replace(minute=minute)


def window_is_final(window: datetime, now: datetime) -> bool:
    end = window + timedelta(minutes=ENERCAST_PERIOD_MINUTES, seconds=WINDOW_GRACE_SECONDS)
    return now >= end


def group_by_window(rows: list) -> dict[datetime, list[tuple]]:
    groups: dict[datetime, list[tuple]] = {}
    for row in rows:
        start = parse_interval_start(row["interval_start"])
        groups.setdefault(window_start_of(start), []).append((row, start))
    return groups


def send_group(
    client: LiveData,
    store: IntervalStore,
    plant_key: str,
    members: list[tuple],
) -> bool:
    """Sendet eine Gruppe als 15-Minuten-Wert bzw. Stummel als 5/10-Minuten-Wert."""
    members = sorted(members, key=lambda item: item[1])
    starts = [start for _, start in members]
    count = len(members)
    power_kw = sum(float(row["power_sent_kw"]) for row, _ in members) / count
    curtailment_w = sum(float(row["curtailment_w"]) for row, _ in members) / count
    period_minutes = PERIOD_MINUTES * count
    # Vollständiges Fenster startet am Raster; ein Stummel startet beim frühesten
    # vorhandenen Intervall, damit der gemessene Zeitraum möglichst gut getroffen wird.
    start = starts[0] if count < GROUP_SIZE else window_start_of(starts[0])
    try:
        client.send(
            plant_key,
            power_kw,
            curtailment_w,
            power_unit="kW",
            start=start,
            period_minutes=period_minutes,
        )
    except (ApiError, ValueError, OSError) as exc:
        message = str(exc)
        for interval_start in starts:
            store.mark_failed(interval_start, message)
        log(
            f"Upload fehlgeschlagen für {format_instant(start)} "
            f"({count}x{PERIOD_MINUTES} min): {exc}"
        )
        return False
    for interval_start in starts:
        store.mark_uploaded(interval_start)
    log(
        f"uploaded {format_instant(start)} +{period_minutes} min "
        f"aus {count} Intervall(en) Leistung={power_kw:.3f} kW "
        f"Abregelung={curtailment_w:.0f} W"
    )
    return True


def flush_pending(
    client: LiveData,
    store: IntervalStore,
    plant_key: str,
    now: datetime | None = None,
) -> int:
    now = now or utc_now()
    rows = store.list_upload_candidates()
    if not rows:
        return 0
    sent = 0
    for window, members in sorted(group_by_window(rows).items()):
        if len(members) >= GROUP_SIZE or window_is_final(window, now):
            if send_group(client, store, plant_key, members):
                sent += len(members)
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
