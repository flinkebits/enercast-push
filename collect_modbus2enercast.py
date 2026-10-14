#!/usr/bin/env python3
"""Liest Modbus im 3-Sekunden-Takt, bildet 5-Minuten-UTC-Mittelwerte und schreibt sie in den Cache."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from interval_store import (
    PERIOD_MINUTES,
    STATUS_PENDING,
    STATUS_SKIPPED,
    IntervalStore,
    floor_interval,
    format_instant,
    utc_now,
)

HOST = "172.20.5.110"
PORT = 502
UNIT_ID = 0
READ_FUNCTION = 3
TICK_SECONDS = 3.0
EXPECTED_SAMPLES = int(PERIOD_MINUTES * 60 / TICK_SECONDS)
MIN_SAMPLES = max(1, EXPECTED_SAMPLES // 4)
MAX_KW = 100_000
INVALID_SIGNED = 0x7FFFFFFF
INVALID_UNSIGNED = 0xFFFFFFFF

POWER = {"name": "Leistung", "address": 40525, "quantity": 2, "signed": True, "gain": 1000, "unit": "kW"}
LIMIT = {"name": "Abregelung", "address": 40738, "quantity": 2, "signed": False, "gain": 10, "unit": "kW"}


def log(message: str) -> None:
    print(f"{format_instant(utc_now())} {message}", flush=True)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("Verbindung geschlossen")
        buf += chunk
    return buf


def read_holding(sock: socket.socket, address: int, quantity: int) -> list[int]:
    pdu = bytes([UNIT_ID, READ_FUNCTION]) + struct.pack(">HH", address, quantity)
    frame = struct.pack(">HHH", 1, 0, len(pdu)) + pdu
    sock.sendall(frame)
    header = recv_exact(sock, 6)
    length = struct.unpack(">HHH", header)[2]
    body = recv_exact(sock, length)
    if len(body) < 2 or body[1] != READ_FUNCTION:
        code = body[2] if len(body) > 2 else "?"
        raise RuntimeError(f"Lesen von {address} abgelehnt ({code})")
    payload = body[3 : 3 + body[2]]
    return [struct.unpack(">H", payload[i : i + 2])[0] for i in range(0, len(payload) - 1, 2)]


def sample_kw(words: list[int], signed: bool, gain: int) -> float | None:
    raw = (words[0] << 16) | words[1]
    if signed:
        if raw == INVALID_SIGNED:
            return None
        if raw & 0x80000000:
            raw -= 0x100000000
    elif raw == INVALID_UNSIGNED:
        return None
    return raw / gain


def connect() -> socket.socket:
    sock = socket.create_connection((HOST, PORT), timeout=3)
    sock.settimeout(3)
    return sock


def close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


@dataclass
class Bucket:
    interval_start: datetime
    powers: list[float] = field(default_factory=list)
    limits: list[float] = field(default_factory=list)


def evaluate_bucket(bucket: Bucket) -> tuple[str, float, float, float, float, str | None]:
    """Returns status, power_mean, power_sent, curtailment_w, and optional skip reason."""
    powers, limits = bucket.powers, bucket.limits
    if len(powers) < MIN_SAMPLES or len(limits) < MIN_SAMPLES:
        reason = (
            f"zu wenige Proben Leistung {len(powers)}/{EXPECTED_SAMPLES}, "
            f"Abregelung {len(limits)}/{EXPECTED_SAMPLES}"
        )
        return STATUS_SKIPPED, 0.0, 0.0, 0.0, 0.0, reason
    power_mean = sum(powers) / len(powers)
    limit_mean = sum(limits) / len(limits)
    if not math.isfinite(power_mean) or not math.isfinite(limit_mean):
        return STATUS_SKIPPED, power_mean, 0.0, 0.0, 0.0, "Mittelwert nicht endlich"
    if limit_mean < 0:
        return STATUS_SKIPPED, power_mean, 0.0, 0.0, 0.0, f"Abregelung={limit_mean:.1f} kW unter 0"
    if power_mean > MAX_KW or limit_mean > MAX_KW:
        return (
            STATUS_SKIPPED,
            power_mean,
            0.0,
            0.0,
            0.0,
            f"Mittelwert über {MAX_KW:.0f} kW",
        )
    power_sent = 0.0 if power_mean < 0 else power_mean
    curtailment_w = limit_mean * 1000.0
    return STATUS_PENDING, power_mean, power_sent, curtailment_w, limit_mean, None


def finalize_bucket(store: IntervalStore, bucket: Bucket) -> None:
    status, power_mean, power_sent, curtailment_w, _limit_mean, reason = evaluate_bucket(bucket)
    start = format_instant(bucket.interval_start)
    if status == STATUS_SKIPPED:
        store.save(
            bucket.interval_start,
            power_mean_kw=power_mean,
            power_sent_kw=0.0,
            curtailment_w=0.0,
            power_samples=len(bucket.powers),
            limit_samples=len(bucket.limits),
            status=STATUS_SKIPPED,
            last_error=reason,
        )
        log(f"Intervall {start} übersprungen: {reason}")
        return
    saved = store.save(
        bucket.interval_start,
        power_mean_kw=power_mean,
        power_sent_kw=power_sent,
        curtailment_w=curtailment_w,
        power_samples=len(bucket.powers),
        limit_samples=len(bucket.limits),
        status=STATUS_PENDING,
    )
    if not saved:
        log(f"Intervall {start} nicht gespeichert (bereits uploaded)")
        return
    power_text = f"Leistung={power_mean:.1f} kW"
    if power_mean < 0:
        power_text = f"Leistung={power_mean:.1f} kW (send={power_sent:.1f} kW)"
    log(
        f"Intervall {start} pending: {power_text} "
        f"Abregelung={curtailment_w:.0f} W "
        f"Proben {len(bucket.powers)}/{EXPECTED_SAMPLES}"
    )


def read_sample(sock: socket.socket | None) -> tuple[socket.socket | None, float | None, float | None]:
    try:
        if sock is None:
            sock = connect()
        power_words = read_holding(sock, POWER["address"], POWER["quantity"])
        limit_words = read_holding(sock, LIMIT["address"], LIMIT["quantity"])
        power = sample_kw(power_words, POWER["signed"], POWER["gain"])
        limit = sample_kw(limit_words, LIMIT["signed"], LIMIT["gain"])
        return sock, power, limit
    except Exception as exc:
        log(f"Modbus-Fehler: {exc}")
        close_socket(sock)
        return None, None, None


def sleep_until_next_tick() -> None:
    now = time.time()
    next_tick = math.floor(now / TICK_SECONDS) * TICK_SECONDS + TICK_SECONDS
    delay = next_tick - now
    if delay > 0:
        time.sleep(delay)


def run_daemon(store: IntervalStore) -> None:
    log(
        f"collect start Modbus {HOST}:{PORT} "
        f"{PERIOD_MINUTES}-Min-UTC Intervall, ~{TICK_SECONDS:g} s Takt"
    )
    current_start = floor_interval(utc_now(), PERIOD_MINUTES)
    bucket = Bucket(current_start)
    sock: socket.socket | None = None
    try:
        while True:
            now = utc_now()
            active_start = floor_interval(now, PERIOD_MINUTES)
            if active_start != bucket.interval_start:
                finalize_bucket(store, bucket)
                bucket = Bucket(active_start)
            sock, power, limit = read_sample(sock)
            if power is not None:
                bucket.powers.append(power)
            if limit is not None:
                bucket.limits.append(limit)
            sleep_until_next_tick()
    finally:
        close_socket(sock)
        store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Modbus sammeln und 5-Min-Intervalle cachen.")
    parser.add_argument(
        "--db",
        type=Path,
        help="SQLite-Pfad (Default: ENERCAST_INTERVAL_DB oder data/modbus_intervals.db)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = args.db
    store = IntervalStore(Path(db_path) if db_path else None)
    try:
        run_daemon(store)
    except KeyboardInterrupt:
        log("beendet")


if __name__ == "__main__":
    main()
