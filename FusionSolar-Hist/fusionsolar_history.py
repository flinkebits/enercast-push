#!/usr/bin/env python3
"""Wirkleistung des SmartLogger als eine CSV.

Der Logger liefert den kumulierten Ertrag total_yield in kWh. Die Leistung
eines Intervalls ist die Differenz zum vorherigen 5-Minuten-Wert, in Watt.
Mit --interval 15 wird daraus der Mittelwert dreier aufeinanderfolgender
5-Minuten-Werte; der Start liegt dann auf Minute 0, 15, 30 oder 45.
Spalten: Start, Ende (Europe/Berlin), Watt.

Aufruf: fusionsolar_history.py [--interval 5|15] [--start JJJJ-MM-TT] [--end JJJJ-MM-TT].
Beide Daten liegen in Europe/Berlin, das Ende ist einschließlich.
Standardstart ist das Netzanschlussdatum der Anlage, Standardende heute.
Standardintervall ist 5 Minuten, Datei data/leistung.csv.
15 Minuten schreibt data/leistung-15min.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
OUT_DIR = ROOT / "data"
CSV_PATH = OUT_DIR / "leistung.csv"
STATE_PATH = OUT_DIR / "state.json"

LOGGER = 63
STEP_MS = 5 * 60 * 1000
QUARTER_MS = 3 * STEP_MS
DAY_MS = 24 * 3600 * 1000
WINDOW_MS = DAY_MS - STEP_MS
GAP_SECONDS = 61
WATT_PER_KWH_STEP = 12 * 1000

COLUMNS = ("Start", "Ende", "Watt")
QUOTA_CODES = {407, 403, 429}


def load_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class FusionSolarError(Exception):
    def __init__(self, fail_code, message):
        super().__init__(message)
        self.fail_code = fail_code


def floor_5min_ms(ms: int) -> int:
    return ms - (ms % STEP_MS)


def window_end_ms(start_ms: int, final_ms: int) -> int:
    return min(start_ms + WINDOW_MS, final_ms)


def parse_instant_ms(text: str) -> int:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    return floor_5min_ms(int(parsed.timestamp() * 1000))


def day_end_ms(text: str) -> int:
    """0 Uhr des Folgetags (Europe/Berlin): der genannte Tag endet eingeschlossen."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    midnight = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return floor_5min_ms(int((midnight + timedelta(days=1)).timestamp() * 1000))


def today_end_ms() -> int:
    tomorrow = datetime.now(BERLIN).date() + timedelta(days=1)
    return day_end_ms(tomorrow.isoformat())


def format_watt(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def interval_labels(start_ms: int, minutes: int = 5) -> tuple[str, str]:
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc).astimezone(BERLIN)
    end = start + timedelta(minutes=minutes)
    pattern = "%Y-%m-%d %H:%M:%S"
    return start.strftime(pattern), end.strftime(pattern)


def format_power_row(start_ms: int, watts: float, minutes: int) -> dict[str, str]:
    label_start, label_end = interval_labels(start_ms, minutes)
    return {"Start": label_start, "Ende": label_end, "Watt": format_watt(watts)}


def power_intervals(
    samples: list[tuple[int, float | None]],
    previous: tuple[int, float] | None,
    start_ms: int,
    end_ms: int,
) -> tuple[list[tuple[int, float]], tuple[int, float] | None]:
    """5-Minuten-Leistung aus aufeinanderfolgenden total_yield-Werten.

    Ein Intervall entsteht nur bei genau fünf Minuten Abstand und nicht fallendem
    Zähler. previous ist der letzte Zählerstand vor diesem Fenster.
    """
    points: list[tuple[int, float]] = []
    if previous is not None:
        points.append(previous)
    for collect_ms, yield_kwh in sorted(samples, key=lambda item: item[0]):
        if yield_kwh is None:
            continue
        slot = floor_5min_ms(collect_ms)
        if points and points[-1][0] == slot:
            points[-1] = (slot, yield_kwh)
            continue
        points.append((slot, yield_kwh))

    series = []
    for earlier, later in zip(points, points[1:]):
        prev_ms, prev_yield = earlier
        cur_ms, cur_yield = later
        if cur_ms - prev_ms != STEP_MS or cur_yield < prev_yield:
            continue
        if prev_ms < start_ms or prev_ms >= end_ms:
            continue
        series.append((prev_ms, (cur_yield - prev_yield) * WATT_PER_KWH_STEP))
    last = points[-1] if points else previous
    return series, last


def watt_rows(
    samples: list[tuple[int, float | None]],
    previous: tuple[int, float] | None,
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict[str, str]], tuple[int, float] | None]:
    series, last = power_intervals(samples, previous, start_ms, end_ms)
    return [format_power_row(start, watts, 5) for start, watts in series], last


def complete_quarters(
    pending: list[tuple[int, float]],
    rows: list[tuple[int, float]],
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Mittelwert je Viertelstunde aus genau drei 5-Minuten-Leistungen.

    Start ist Minute 0, 15, 30 oder 45. Eine unvollständige Viertelstunde am
    Ende bleibt pending, damit sie im nächsten Fenster fertig wird. Lücken
    davor fallen weg.
    """
    points = list(pending) + list(rows)
    if not points:
        return [], []
    grouped: dict[int, dict[int, float]] = {}
    for start_ms, watts in points:
        quarter = start_ms - (start_ms % QUARTER_MS)
        grouped.setdefault(quarter, {})[start_ms - quarter] = watts
    last = max(grouped)
    done = []
    still: list[tuple[int, float]] = []
    offsets = (0, STEP_MS, 2 * STEP_MS)
    for quarter in sorted(grouped):
        found = grouped[quarter]
        full = all(offset in found for offset in offsets)
        if quarter == last and not full:
            still = [(quarter + offset, found[offset]) for offset in sorted(found)]
            continue
        if full:
            done.append((quarter, sum(found[offset] for offset in offsets) / 3))
    return done, still


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", flush=True)


class Client:
    def __init__(self, host: str, user: str, system_code: str):
        self.base = "https://" + host.removeprefix("https://").removeprefix("http://").strip("/")
        self.user = user
        self.system_code = system_code
        self.token = ""
        self._last_post = 0.0

    def login(self) -> None:
        status, headers, body = self._raw(
            "/thirdData/login",
            {"userName": self.user, "systemCode": self.system_code},
            token="",
        )
        self._raise_if_failed(status, body)
        token = headers.get("xsrf-token") or headers.get("XSRF-TOKEN")
        if not token:
            raise FusionSolarError(0, "Login ohne XSRF-TOKEN.")
        self.token = token

    def post(self, path: str, payload: dict) -> dict:
        self._gap()
        status, _headers, body = self._raw(path, payload, self.token)
        if isinstance(body, dict) and body.get("failCode") == 305:
            log("Token abgelaufen, neues Login.")
            self.login()
            self._gap()
            status, _headers, body = self._raw(path, payload, self.token)
        self._raise_if_failed(status, body)
        return body

    def _gap(self) -> None:
        wait = GAP_SECONDS - (time.monotonic() - self._last_post)
        if self._last_post and wait > 0:
            time.sleep(wait)

    def _raw(self, path: str, payload: dict, token: str) -> tuple[int, dict, dict]:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["XSRF-TOKEN"] = token
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        self._last_post = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                parsed = json.loads(raw) if raw else {}
                return response.status, dict(response.headers), parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            parsed = json.loads(raw) if raw else {}
            return exc.code, dict(exc.headers), parsed

    @staticmethod
    def _raise_if_failed(status: int, body: dict) -> None:
        if not isinstance(body, dict):
            raise FusionSolarError(status, "Antwort ist kein JSON-Objekt.")
        code = body.get("failCode")
        if body.get("success") is False or (code not in (None, 0)):
            message = body.get("message") or f"failCode {code}"
            raise FusionSolarError(int(code or status), str(message))


def plants(client: Client) -> list[dict]:
    page = 1
    found = []
    while True:
        body = client.post("/thirdData/stations", {"pageNo": page})
        data = body.get("data") or {}
        found.extend(data.get("list") or [])
        if page >= int(data.get("pageCount") or 1):
            return found
        page += 1


def choose_plant(found: list[dict], wanted: str) -> dict:
    if wanted:
        matches = [
            plant
            for plant in found
            if wanted in (str(plant.get("plantCode") or ""), str(plant.get("plantName") or ""))
        ]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(str(plant.get("plantName")) for plant in found)
        raise SystemExit(f"Anlage {wanted!r} nicht eindeutig. Gebunden: {names}")
    if len(found) == 1:
        return found[0]
    names = ", ".join(
        f"{plant.get('plantName')} ({plant.get('plantCode')})" for plant in found
    )
    raise SystemExit(f"Mehrere Anlagen. FUSIONSOLAR_PLANT setzen. Gebunden: {names}")


def devices(client: Client, plant_code: str) -> list[dict]:
    body = client.post("/thirdData/getDevList", {"stationCodes": plant_code})
    data = body.get("data") or []
    return data if isinstance(data, list) else []


def smartlogger(device_list: list[dict]) -> dict:
    found = [item for item in device_list if item.get("devTypeId") == LOGGER and item.get("devDn")]
    if len(found) == 1:
        return found[0]
    names = ", ".join(str(item.get("devName")) for item in found) or "keiner"
    raise SystemExit(f"Erwartet einen SmartLogger (Typ {LOGGER}), gefunden: {names}.")


def history(client: Client, dev_dn: str, start_ms: int, end_ms: int) -> list[tuple[int, float | None]]:
    body = client.post(
        "/rest/openapi/pvms/nbi/v1/device/history",
        {
            "devDn": dev_dn,
            "devTypeId": LOGGER,
            "startTime": start_ms,
            "endTime": end_ms + STEP_MS - 1,
        },
    )
    samples = []
    for item in body.get("data") or []:
        item_map = item.get("dataItems") or item.get("dataItemMap") or {}
        yield_kwh = item_map.get("total_yield")
        collect = item.get("collectTime")
        if collect is None:
            continue
        samples.append((int(collect), None if yield_kwh is None else float(yield_kwh)))
    return samples


def output_paths(interval: int) -> tuple[Path, Path]:
    if interval == 15:
        return OUT_DIR / "leistung-15min.csv", OUT_DIR / "state-15min.json"
    return CSV_PATH, STATE_PATH


def load_state(path: Path = STATE_PATH) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def append_rows(rows: list[dict[str, str]], path: Path = CSV_PATH) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def credentials() -> tuple[str, str, str, str]:
    env = load_env(ENV_PATH)
    host = env.get("FUSIONSOLAR_HOST", "")
    user = env.get("FUSIONSOLAR_USER", "")
    code = env.get("FUSIONSOLAR_SYSTEM_CODE", "")
    if not host or not user or not code:
        raise SystemExit(
            "In FusionSolar-Hist/.env fehlen FUSIONSOLAR_HOST, FUSIONSOLAR_USER oder FUSIONSOLAR_SYSTEM_CODE."
        )
    return host, user, code, env.get("FUSIONSOLAR_PLANT", "")


def resume_state(state: dict | None, final_ms: int) -> dict | None:
    if not state or state.get("dev_dn") is None:
        return None
    if state.get("end_ms") != final_ms or state.get("next_ms", 0) >= final_ms:
        return None
    return state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wirkleistung des SmartLogger als CSV, in 5- oder 15-Minuten-Intervallen."
    )
    parser.add_argument(
        "--interval",
        type=int,
        choices=(5, 15),
        default=5,
        help="Intervall in Minuten. Bei 15 ist Watt der Mittelwert dreier 5-Minuten-Werte",
    )
    parser.add_argument(
        "--start", help="Startdatum JJJJ-MM-TT, Standard: Netzanschlussdatum der Anlage"
    )
    parser.add_argument(
        "--end", help="Enddatum JJJJ-MM-TT einschließlich, Standard: heute"
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    interval = args.interval
    csv_file, state_file = output_paths(interval)
    host, user, code, wanted = credentials()
    client = Client(host, user, code)
    final_ms = day_end_ms(args.end) if args.end else today_end_ms()
    state = resume_state(load_state(state_file), final_ms)
    pending: list[tuple[int, float]] = []

    def checkpoint(at_ms: int, prev: tuple[int, float] | None) -> dict:
        saved = {
            "plant_name": plant_name,
            "dev_dn": dev_dn,
            "dev_name": dev_name,
            "next_ms": at_ms,
            "end_ms": final_ms,
            "prev_ms": None if prev is None else prev[0],
            "prev_yield": None if prev is None else prev[1],
        }
        if interval == 15:
            saved["pending"] = [[start_ms, watts] for start_ms, watts in pending]
        return saved

    if state:
        plant_name = state["plant_name"]
        dev_dn = state["dev_dn"]
        dev_name = state["dev_name"]
        next_ms = state["next_ms"]
        previous = None
        if state.get("prev_ms") is not None and state.get("prev_yield") is not None:
            previous = (int(state["prev_ms"]), float(state["prev_yield"]))
        if interval == 15:
            pending = [(int(item[0]), float(item[1])) for item in state.get("pending") or []]
        log(f"Fortsetzung {plant_name}, {dev_name} ab {interval_labels(next_ms)[0]}")
        client.login()
    else:
        client.login()
        found = plants(client)
        if not found:
            raise SystemExit("Keine Anlage für dieses API-Konto.")
        plant = choose_plant(found, wanted)
        plant_name = str(plant.get("plantName") or plant["plantCode"])
        device_list = devices(client, str(plant["plantCode"]))
        logger = smartlogger(device_list)
        dev_dn = str(logger["devDn"])
        dev_name = str(logger.get("devName") or dev_dn)
        connected = plant.get("gridConnectionDate")
        next_ms = parse_instant_ms(connected) if connected else floor_5min_ms(0)
        if args.start:
            next_ms = max(next_ms, parse_instant_ms(args.start))
        if next_ms >= final_ms:
            raise SystemExit(
                f"Start {interval_labels(next_ms)[0]} liegt nicht vor dem Ende {interval_labels(final_ms)[0]}."
            )
        previous = None
        if csv_file.is_file():
            csv_file.unlink()
        log(f"Anlage {plant_name}, SmartLogger {dev_name} ({dev_dn})")
        save_state(checkpoint(next_ms, previous), state_file)

    while next_ms < final_ms:
        chunk_end = window_end_ms(next_ms, final_ms)
        label = f"{interval_labels(next_ms)[0]} .. {interval_labels(chunk_end)[0]}"
        try:
            samples = history(client, dev_dn, next_ms, chunk_end)
        except FusionSolarError as exc:
            if exc.fail_code in QUOTA_CODES:
                log(f"Kontingent aufgebraucht, CSV bleibt bei {csv_file}")
                return
            raise
        series, previous = power_intervals(samples, previous, next_ms, chunk_end)
        if interval == 15:
            series, pending = complete_quarters(pending, series)
            rows = [format_power_row(start_ms, watts, 15) for start_ms, watts in series]
        else:
            rows = [format_power_row(start_ms, watts, 5) for start_ms, watts in series]
        append_rows(rows, csv_file)
        next_ms = chunk_end
        save_state(checkpoint(next_ms, previous), state_file)
        log(f"{label}: {len(rows)} Zeilen, Datei {csv_file.name}")

    log(f"Fertig bis {interval_labels(final_ms)[0]}, Datei {csv_file}")


if __name__ == "__main__":
    try:
        run()
    except FusionSolarError as exc:
        raise SystemExit(f"FusionSolar {exc.fail_code}: {exc}") from exc
