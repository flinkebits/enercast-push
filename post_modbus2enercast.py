#!/usr/bin/env python3
"""Misst Wirkleistung und Abregelung und sendet einen Minutenwert an enercast.

30 Sekunden lang jede Sekunde, dann der Mittelwert. Ein Aufruf sendet einmal
und endet. cron startet das Skript minütlich. Es wird ausschließlich
Modbus-Funktion 03 (Holding-Register lesen) gesendet.
"""

from __future__ import annotations

import math
import socket
import struct
import time
from datetime import datetime, timezone

from enercast_live import ENV_PATH, ApiError, LiveData, load_env

HOST = "172.20.5.110"
PORT = 502
UNIT_ID = 0
READ_FUNCTION = 3
SAMPLE_COUNT = 30
INTERVAL_SECONDS = 1.0
MIN_SAMPLES = 20
MAX_KW = 100_000
INVALID_SIGNED = 0x7FFFFFFF
INVALID_UNSIGNED = 0xFFFFFFFF

POWER = {"name": "Leistung", "address": 40525, "quantity": 2, "signed": True, "gain": 1000, "unit": "kW"}
LIMIT = {"name": "Abregelung", "address": 40738, "quantity": 2, "signed": False, "gain": 10, "unit": "kW"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def abort(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)
    raise SystemExit(1)


def plant_key_from_env() -> str:
    value = load_env(ENV_PATH).get("ENERCAST_PLANT_KEY", "")
    if not isinstance(value, str) or not value.strip():
        abort("ENERCAST_PLANT_KEY muss in der .env stehen.")
    return value.strip()


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


def collect() -> tuple[list[float], list[float]]:
    powers: list[float] = []
    limits: list[float] = []
    sock: socket.socket | None = None
    origin = time.monotonic()
    try:
        for index in range(SAMPLE_COUNT):
            wait = origin + index * INTERVAL_SECONDS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                if sock is None:
                    sock = connect()
                power_words = read_holding(sock, POWER["address"], POWER["quantity"])
                limit_words = read_holding(sock, LIMIT["address"], LIMIT["quantity"])
                power = sample_kw(power_words, POWER["signed"], POWER["gain"])
                limit = sample_kw(limit_words, LIMIT["signed"], LIMIT["gain"])
            except Exception as exc:
                print(f"{utc_now()} Fehler: {exc}", flush=True)
                close_socket(sock)
                sock = None
                continue
            if power is not None:
                powers.append(power)
            if limit is not None:
                limits.append(limit)
    finally:
        close_socket(sock)
    return powers, limits


def plausible_means(powers: list[float], limits: list[float]) -> tuple[float, float]:
    if len(powers) < MIN_SAMPLES or len(limits) < MIN_SAMPLES:
        abort(
            "zu wenige Proben: "
            f"Leistung {len(powers)}/{SAMPLE_COUNT}, "
            f"Abregelung {len(limits)}/{SAMPLE_COUNT}, nichts gesendet"
        )
    power_mean = sum(powers) / len(powers)
    limit_mean = sum(limits) / len(limits)
    if not math.isfinite(power_mean) or not math.isfinite(limit_mean):
        abort("Mittelwert ist nicht endlich, nichts gesendet")
    if limit_mean < 0:
        abort(f"Abregelung={limit_mean:.1f} kW liegt unter 0, nichts gesendet")
    if power_mean > MAX_KW or limit_mean > MAX_KW:
        abort(
            f"Mittelwert über {MAX_KW:.0f} kW: "
            f"Leistung={power_mean:.1f} kW Abregelung={limit_mean:.1f} kW, nichts gesendet"
        )
    return power_mean, limit_mean


def report(
    power_mean: float,
    curtailment_w: float,
    powers: list[float],
    limits: list[float],
    sent: datetime,
) -> None:
    stamp = sent.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    samples = (
        f"Proben Leistung {len(powers)}/{SAMPLE_COUNT}, "
        f"Abregelung {len(limits)}/{SAMPLE_COUNT}"
    )
    if power_mean < 0:
        power_text = f"Leistung={power_mean:.1f} kW, gesendet als 0.0 kW"
    else:
        power_text = f"Leistung={power_mean:.1f} kW"
    print(
        f"{utc_now()} {power_text} Abregelung={curtailment_w:.0f} W ({samples}) gesendet für {stamp}",
        flush=True,
    )


def main() -> None:
    try:
        client = LiveData.from_env()
    except ValueError as exc:
        abort(str(exc))
    plant_key = plant_key_from_env()
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    powers, limits = collect()
    power_mean, limit_mean = plausible_means(powers, limits)
    sent_power = 0.0 if power_mean < 0 else power_mean
    curtailment_w = limit_mean * 1000
    try:
        sent = client.send(plant_key, sent_power, curtailment_w, power_unit="kW", start=minute)
    except (ApiError, ValueError, OSError) as exc:
        abort(f"nicht gesendet: {exc}")
    report(power_mean, curtailment_w, powers, limits, sent)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("beendet", flush=True)
