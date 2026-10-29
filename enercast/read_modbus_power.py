#!/usr/bin/env python3
"""Liest Wirkleistung und Abregelung vom Huawei-SmartLogger.

Einmal pro Sekunde eine Zeile auf der Konsole. Es wird ausschließlich
Modbus-Funktion 03 (Holding-Register lesen) gesendet.
"""

from __future__ import annotations

import socket
import struct
import time
from datetime import datetime, timezone

HOST = "172.20.5.110"
PORT = 502
UNIT_ID = 0
INTERVAL_SECONDS = 1.0
READ_FUNCTION = 3

POWER = {"name": "Leistung", "address": 40525, "quantity": 2, "signed": True, "gain": 1000, "unit": "kW"}
LIMIT = {"name": "Abregelung", "address": 40738, "quantity": 2, "signed": False, "gain": 10, "unit": "kW"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def scaled(words: list[int], signed: bool, gain: int) -> float:
    raw = (words[0] << 16) | words[1]
    if signed and raw & 0x80000000:
        raw -= 0x100000000
    return raw / gain


def format_point(sock: socket.socket, point: dict) -> str:
    words = read_holding(sock, point["address"], point["quantity"])
    value = scaled(words, point["signed"], point["gain"])
    return f"{point['name']}={value:.1f} {point['unit']}"


def connect() -> socket.socket:
    sock = socket.create_connection((HOST, PORT), timeout=3)
    sock.settimeout(3)
    return sock


def main() -> None:
    print(
        f"lese {HOST}:{PORT} jede {INTERVAL_SECONDS:g} s, nur Funktion {READ_FUNCTION}",
        flush=True,
    )
    sock: socket.socket | None = None
    while True:
        started = time.monotonic()
        try:
            if sock is None:
                sock = connect()
            line = " ".join(format_point(sock, point) for point in (POWER, LIMIT))
            print(f"{utc_now()} {line}", flush=True)
        except Exception as exc:
            print(f"{utc_now()} Fehler: {exc}", flush=True)
            if sock is not None:
                sock.close()
                sock = None
        remaining = INTERVAL_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("beendet", flush=True)
