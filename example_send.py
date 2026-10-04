#!/usr/bin/env python3
"""Beispiel: eine Minute Wirkleistung und Abregelung von der Kommandozeile senden."""

import argparse
import sys
import urllib.error

from enercast_live import ApiError, LiveData


def main():
    parser = argparse.ArgumentParser(
        description="Schreibt eine Minute Wirkleistung und PV-Abregelung nach enercast."
    )
    parser.add_argument("--asset", required=True, help="Anlagenschlüssel, z. B. Feld0")
    parser.add_argument("--power", required=True, type=float, help="Mittlere Wirkleistung")
    parser.add_argument(
        "--curtailment-w",
        required=True,
        type=float,
        help="Verfügbare Erzeugungskapazität in Watt",
    )
    parser.add_argument("--power-unit", default="W", choices=("W", "kW"))
    parser.add_argument("--start", help="Intervallanfang in UTC, z. B. 2026-10-04T08:46:00Z")
    args = parser.parse_args()

    try:
        client = LiveData.from_env()
        start_at = client.send(
            args.asset,
            power=args.power,
            curtailment_w=args.curtailment_w,
            power_unit=args.power_unit,
            start=args.start,
        )
    except (ValueError, ApiError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Verbindung fehlgeschlagen: {exc.reason}", file=sys.stderr)
        return 1

    print(f"Gesendet für {start_at.strftime('%Y-%m-%dT%H:%M:%SZ')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
