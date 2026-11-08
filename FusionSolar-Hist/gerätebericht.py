#!/usr/bin/env python3
"""PDF-Bericht über die FusionSolar-Anlagen: Logger, Wechselrichter, Parameter.

Liest alle Anlagen des API-Kontos (POST /thirdData/stations), pro Anlage die
Geräteliste (POST /thirdData/getDevList) und für SmartLogger und Wechselrichter
die Parameter (POST /thirdData/getDevRealKpi, bis 100 Geräte je Aufruf). Lehnt
die Schnittstelle den Loggertyp ab, kommt der neueste 5-Minuten-Wert der
letzten 24 Stunden aus der NBI-Historie. Daraus wird ein PDF mit eingebetteter
DejaVu Sans: Titel und Datum, je Anlage Stammdaten, Geräteübersicht und
Parametertabellen.

Aufruf: gerätebericht.py [-h] [-L] [--plant NAME_ODER_CODE] [--out DATEI.pdf]
Zugangsdaten kommen aus der .env neben diesem Skript (siehe .env.example).
Ohne FUSIONSOLAR_PLANT werden alle Anlagen des Kontos berichtet.
-L listet die Anlagen und schreibt kein PDF.
Standard-Ausgabe: data/geraetebericht-<Zeitstempel>.pdf neben diesem Skript.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fpdf import FPDF
from fpdf.enums import CellBordersLayout
from fpdf.fonts import FontFace

BERLIN = ZoneInfo("Europe/Berlin")
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
OUT_DIR = ROOT / "data"
FONT_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

LOGGER_TYPES = (2, 63)  # 2 SmartLogger, 63 verteilter SmartLogger
INVERTER_TYPES = (1, 38)  # 1 String-Wechselrichter, 38 Haushalts-Wechselrichter
PARAM_TYPES = LOGGER_TYPES + INVERTER_TYPES
TYPE_LABELS = {
    1: "String-Wechselrichter",
    2: "SmartLogger",
    38: "Haushalts-Wechselrichter",
    63: "SmartLogger (verteilt)",
}
QUOTA_CODES = {407, 403, 429}
GAP_SECONDS_DEFAULT = 2.0
QUOTA_WAIT_SECONDS = 61
MAX_DEVICES_PER_KPI_CALL = 100
TABLE_PT = 10.5
INVERTER_TABLE_PT = 8.5
PETROL = (27, 79, 114)
INK = (30, 41, 51)
MUTED = (70, 80, 90)
ZEBRA = (236, 240, 243)
RULE = (176, 190, 197)

# Einheiten und deutsche Bezeichnungen aus doc/FusionSolar/README.md.
UNITS = {
    "active_power": "kW",
    "reactive_power": "kVar",
    "mppt_power": "kW",
    "day_cap": "kWh",
    "total_cap": "kWh",
    "total_yield": "kWh",
    "elec_freq": "Hz",
    "power_factor": "—",
    "temperature": "°C",
    "efficiency": "%",
    "inverter_state": "—",
    "open_time": "ms",
    "close_time": "ms",
    "total_power_consumption": "W",
    "total_supply_from_grid": "kWh",
    "total_feed_in_to_grid": "kWh",
    "ac_total_charge_energy": "kWh",
    "ac_total_discharge_energy": "kWh",
    "total_charge": "kWh",
    "total_discharge": "kWh",
    "insulation_resistance": "MΩ",
}
LABELS = {
    "active_power": "Wirkleistung",
    "reactive_power": "Blindleistung",
    "mppt_power": "DC-Eingangsleistung (Summe MPPT)",
    "day_cap": "Ertrag seit Tagesbeginn",
    "total_cap": "Gesamtertrag",
    "total_yield": "Gesamtertrag (kumuliert)",
    "elec_freq": "Netzfrequenz",
    "power_factor": "Leistungsfaktor",
    "temperature": "Innentemperatur",
    "efficiency": "Wirkungsgrad (Hersteller)",
    "inverter_state": "Betriebszustand",
    "open_time": "Startzeit",
    "close_time": "Stoppzeit",
    "total_power_consumption": "Gesamtverbrauch (Leistung)",
    "total_supply_from_grid": "Energiebezug aus dem Netz",
    "total_feed_in_to_grid": "Einspeisung ins Netz",
    "ac_total_charge_energy": "AC-Ladeenergie gesamt",
    "ac_total_discharge_energy": "AC-Entladeenergie gesamt",
    "total_charge": "Gesamtladung",
    "total_discharge": "Gesamtentladung",
    "insulation_resistance": "Isolationswiderstand",
}
STATE_LABELS = {
    512: "netzparallel",
    513: "netzparallel, leistungsbegrenzt",
    514: "Eigen-Derating",
    772: "Abschaltung wegen Leistungsbegrenzung",
    45056: "Kommunikationsunterbrechung",
}
PLANT_FIELDS = (
    ("plantName", "Anlagenname"),
    ("plantCode", "Anlagencode"),
    ("gridConnectionDate", "Netzanschlussdatum"),
    ("address", "Adresse"),
    ("latitude", "Breitengrad"),
    ("longitude", "Längengrad"),
    ("capacity", "Installierte Leistung (kW)"),
)
def table_headings(size_pt: float) -> FontFace:
    return FontFace(
        family="DejaVu",
        emphasis="BOLD",
        size_pt=size_pt,
        color=(255, 255, 255),
        fill_color=PETROL,
    )


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


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", flush=True)


class Client:
    def __init__(self, host: str, user: str, system_code: str, gap_seconds: float = GAP_SECONDS_DEFAULT):
        self.base = "https://" + host.removeprefix("https://").removeprefix("http://").strip("/")
        self.user = user
        self.system_code = system_code
        self.gap_seconds = gap_seconds
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

    def post(self, path: str, payload: dict, retries: int = 3) -> dict:
        self._gap()
        status, _headers, body = self._raw(path, payload, self.token)
        if isinstance(body, dict) and body.get("failCode") == 305:
            log("Token abgelaufen, neues Login.")
            self.login()
            self._gap()
            status, _headers, body = self._raw(path, payload, self.token)
        if isinstance(body, dict) and body.get("failCode") in QUOTA_CODES and retries > 0:
            log(f"Kontingent/Abstand fuer {path} (failCode {body.get('failCode')}), warte {QUOTA_WAIT_SECONDS} s.")
            time.sleep(QUOTA_WAIT_SECONDS)
            return self.post(path, payload, retries - 1)
        self._raise_if_failed(status, body)
        return body

    def _gap(self) -> None:
        wait = self.gap_seconds - (time.monotonic() - self._last_post)
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


def select_plants(found: list[dict], wanted: str) -> list[dict]:
    if not wanted:
        return found
    matches = [
        plant
        for plant in found
        if wanted in (str(plant.get("plantCode") or ""), str(plant.get("plantName") or ""))
    ]
    if not matches:
        names = ", ".join(
            f"{plant.get('plantName')} ({plant.get('plantCode')})" for plant in found
        )
        raise SystemExit(f"Anlage {wanted!r} nicht gefunden. Gebunden: {names}")
    return matches


def devices(client: Client, plant_code: str) -> list[dict]:
    body = client.post("/thirdData/getDevList", {"stationCodes": plant_code})
    data = body.get("data") or []
    return data if isinstance(data, list) else []


def dev_type(dev: dict) -> int:
    try:
        return int(dev.get("devTypeId"))
    except (TypeError, ValueError):
        return -1


def device_name(dev: dict) -> str:
    return str(dev.get("devName") or dev.get("devDn") or dev.get("id") or "")


def devices_by_name(dev_list: list[dict]) -> list[dict]:
    """Geräte alphabetisch nach Name, ohne Groß-/Kleinschreibung."""
    return sorted(dev_list, key=lambda dev: device_name(dev).casefold())


def group_devices(dev_list: list[dict]) -> list[tuple[int, list[dict]]]:
    """Geräte nach Typ gruppiert: zuerst Logger, dann Wechselrichter, dann Rest.

    Innerhalb eines Typs nach Name.
    """
    groups: dict[int, list[dict]] = {}
    for dev in dev_list:
        groups.setdefault(dev_type(dev), []).append(dev)
    order = [t for t in PARAM_TYPES if t in groups]
    order += sorted(t for t in groups if t not in PARAM_TYPES)
    return [(t, devices_by_name(groups[t])) for t in order]


def real_kpi(client: Client, type_id: int, dev_ids: list[str]) -> dict[str, dict]:
    """Echtzeit-Parameter je devId. Ein abgelehnter Typ lässt die Map leer."""
    result: dict[str, dict] = {}
    for start in range(0, len(dev_ids), MAX_DEVICES_PER_KPI_CALL):
        chunk = dev_ids[start : start + MAX_DEVICES_PER_KPI_CALL]
        if not chunk:
            continue
        try:
            body = client.post(
                "/thirdData/getDevRealKpi",
                {"devIds": ",".join(chunk), "devTypeId": str(type_id)},
            )
        except FusionSolarError as exc:
            log(f"getDevRealKpi Typ {type_id}: failCode {exc.fail_code}: {exc}")
            continue
        for item in body.get("data") or []:
            dev_id = item.get("devId")
            if dev_id is not None:
                result[str(dev_id)] = item.get("dataItemMap") or {}
    return result


def logger_parameters(client: Client, dev: dict) -> dict:
    """Neuester 5-Minuten-Datensatz des Loggers aus der Historie-Schnittstelle.

    getDevRealKpi lehnt den Loggertyp 63 oft mit failCode 20013 ab. Die neuere
    NBI-Historie unterstützt ihn.
    """
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    body = client.post(
        "/rest/openapi/pvms/nbi/v1/device/history",
        {
            "devDn": str(dev.get("devDn")),
            "devTypeId": dev_type(dev),
            "startTime": end_ms - 24 * 3600 * 1000,
            "endTime": end_ms,
        },
    )
    data = body.get("data") or []
    if not data:
        return {}
    last = data[-1]
    return last.get("dataItems") or last.get("dataItemMap") or {}


def collect_report(client: Client, wanted: str) -> list[dict]:
    """Pro Anlage: Stammdaten, Geräteliste und Parameter der Logger und Wechselrichter."""
    found = plants(client)
    if not found:
        raise SystemExit("Keine Anlage für dieses API-Konto.")
    selected = select_plants(found, wanted)
    report = []
    for plant in selected:
        code = str(plant.get("plantCode") or "")
        dev_list = devices(client, code)
        kpi: dict[str, dict] = {}
        source: dict[str, str] = {}
        for type_id in PARAM_TYPES:
            devs_t = [d for d in dev_list if dev_type(d) == type_id]
            ids = [str(d["id"]) for d in devs_t if d.get("id") is not None]
            if not ids:
                continue
            kpi.update(real_kpi(client, type_id, ids))
            for dev_id in ids:
                if dev_id in kpi:
                    source[dev_id] = "real"
        for dev in dev_list:
            if dev_type(dev) not in LOGGER_TYPES:
                continue
            dev_id = str(dev.get("id"))
            if dev_id in kpi or not dev.get("devDn"):
                continue
            try:
                kpi[dev_id] = logger_parameters(client, dev)
                source[dev_id] = "history"
            except FusionSolarError as exc:
                log(f"Logger {dev.get('devName')}: Historie failCode {exc.fail_code}: {exc}")
        report.append({"plant": plant, "devices": dev_list, "kpi": kpi, "kpi_source": source})
        log(
            f"Anlage {plant.get('plantName')} ({code}): {len(dev_list)} Geräte, "
            f"{len(kpi)} mit Parametern"
        )
    return report


def credentials() -> tuple[str, str, str, str, float]:
    env = load_env(ENV_PATH)
    host = env.get("FUSIONSOLAR_HOST", "")
    user = env.get("FUSIONSOLAR_USER", "")
    code = env.get("FUSIONSOLAR_SYSTEM_CODE", "")
    if not host or not user or not code:
        raise SystemExit(
            "In FusionSolar-Hist/.env fehlen FUSIONSOLAR_HOST, FUSIONSOLAR_USER oder FUSIONSOLAR_SYSTEM_CODE."
        )
    try:
        gap = float(env.get("FUSIONSOLAR_GAP_SECONDS", GAP_SECONDS_DEFAULT))
    except ValueError:
        gap = GAP_SECONDS_DEFAULT
    return host, user, code, env.get("FUSIONSOLAR_PLANT", ""), gap


def unit_for(key: str) -> str:
    if key in UNITS:
        return UNITS[key]
    match = re.fullmatch(r"([abc])_([ui])", key)
    if match:
        return "V" if match.group(2) == "u" else "A"
    if re.fullmatch(r"(ab|bc|ca)_u", key):
        return "V"
    match = re.fullmatch(r"pv(\d+)_[ui]", key)
    if match:
        return "V" if key.endswith("_u") else "A"
    if re.fullmatch(r"mppt_\d+_cap", key):
        return "kWh"
    return "—"


def label_for(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    match = re.fullmatch(r"([abc])_([ui])", key)
    if match:
        phase = match.group(1).upper()
        return f"Außenleiter {phase} Spannung" if match.group(2) == "u" else f"Außenleiter {phase} Strom"
    match = re.fullmatch(r"(ab|bc|ca)_u", key)
    if match:
        return f"verkettete Spannung {match.group(1).upper()}"
    match = re.fullmatch(r"pv(\d+)_[ui]", key)
    if match:
        return f"PV-String {match.group(1)} Spannung" if key.endswith("_u") else f"PV-String {match.group(1)} Strom"
    match = re.fullmatch(r"mppt_(\d+)_cap", key)
    if match:
        return f"DC-Ertrag MPPT {match.group(1)}"
    return key


def format_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


def parameter_rows(data_item_map: dict) -> list[tuple[str, str, str]]:
    """(Bezeichnung, Wert, Einheit) je belegtem Parameter."""
    rows = []
    for key in sorted(data_item_map):
        value = data_item_map[key]
        if value is None:
            continue
        text = format_value(value)
        if key == "inverter_state":
            try:
                meaning = STATE_LABELS.get(int(value))
            except (TypeError, ValueError):
                meaning = None
            if meaning:
                text = f"{text} ({meaning})"
        rows.append((label_for(key), text, unit_for(key)))
    return rows


def format_date_value(value) -> str:
    """gridConnectionDate kommt als ms-Epoch oder als ISO-String."""
    text = str(value)
    try:
        return datetime.fromtimestamp(int(text) / 1000, BERLIN).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        pass
    try:
        return datetime.fromisoformat(text).astimezone(BERLIN).strftime("%Y-%m-%d")
    except ValueError:
        return text


def plant_rows(plant: dict) -> list[tuple[str, str]]:
    rows = []
    for key, label in PLANT_FIELDS:
        value = plant.get(key)
        if value in (None, ""):
            continue
        if key == "gridConnectionDate":
            value = format_date_value(value)
        rows.append((label, format_value(value)))
    return rows


def plant_heading(plant: dict) -> str:
    return str(plant.get("plantName") or plant.get("plantCode") or "Anlage")


def _with_unit(value: str, unit: str) -> str:
    return value if unit in ("", "—") else f"{value} {unit}"


def inverter_parameter_grid(data_item_map: dict) -> list[list[str]]:
    """PV-Strings zuerst, numerisch, Spannung und Strom eines Strings in einer Zeile.

    Danach die übrigen Parameter, ebenfalls zu zweit je Zeile.
    """
    strings: dict[int, dict[str, object]] = {}
    rest = {}
    for key, value in data_item_map.items():
        match = re.fullmatch(r"pv(\d+)_[ui]", key)
        if match:
            if value is None:
                continue
            strings.setdefault(int(match.group(1)), {})[key[-1]] = value
            continue
        rest[key] = value
    grid = []
    for number in sorted(strings):
        side = strings[number]
        voltage = side.get("u")
        current = side.get("i")
        grid.append([
            f"PV-String {number} Spannung",
            "" if voltage is None else _with_unit(format_value(voltage), "V"),
            f"PV-String {number} Strom",
            "" if current is None else _with_unit(format_value(current), "A"),
        ])
    tail = []
    for label, value, unit in parameter_rows(rest):
        tail.append((label, _with_unit(value, unit)))
    for index in range(0, len(tail), 2):
        left = tail[index]
        right = tail[index + 1] if index + 1 < len(tail) else ("", "")
        grid.append([left[0], left[1], right[0], right[1]])
    return grid


def parameter_grid(rows: list[tuple[str, str, str]]) -> list[list[str]]:
    """Parametertabelle zweispaltig: links und rechts je Bezeichnung und Wert.

    Die Einheit steht am Wert. Eine ungerade Anzahl lässt die rechte Spalte leer.
    """
    cells = []
    for label, value, unit in rows:
        cells.append((label, _with_unit(value, unit)))
    grid = []
    for index in range(0, len(cells), 2):
        left = cells[index]
        right = cells[index + 1] if index + 1 < len(cells) else ("", "")
        grid.append([left[0], left[1], right[0], right[1]])
    return grid


def format_plant_list(found: list[dict]) -> str:
    count = len(found)
    word = "Anlage" if count == 1 else "Anlagen"
    lines = [f"{count} {word}"]
    for plant in found:
        name = plant_heading(plant)
        code = str(plant.get("plantCode") or "—")
        extra = []
        if plant.get("capacity") not in (None, ""):
            extra.append(f"{format_value(plant.get('capacity'))} kW")
        if plant.get("gridConnectionDate") not in (None, ""):
            extra.append(format_date_value(plant["gridConnectionDate"]))
        tail = f"  ({', '.join(extra)})" if extra else ""
        lines.append(f"{name}  {code}{tail}")
    return "\n".join(lines)


def footer_parts(plant_name: str, created: datetime, page: int, total: int | str) -> tuple[str, str, str, str]:
    """Links Anlagenname, Mitte Erstellzeit, darunter der Hinweis, rechts Seite n von m."""
    left = plant_name or "—"
    center = f"Erstellt am {created:%d.%m.%Y, %H:%M} Uhr"
    note = "Messwerte zum Erstellzeitpunkt"
    right = f"Seite {page} von {total}"
    return left, center, note, right


class ReportPDF(FPDF):
    def __init__(self, created: datetime):
        super().__init__(orientation="P", unit="mm", format="A4")
        if not FONT_REGULAR.is_file() or not FONT_BOLD.is_file():
            raise SystemExit("DejaVu Sans fehlt. Paket fonts-dejavu-core installieren.")
        self.add_font("DejaVu", "", str(FONT_REGULAR))
        self.add_font("DejaVu", "B", str(FONT_BOLD))
        self.created = created
        self.current_plant = ""
        self.plant_by_page: dict[int, str] = {}
        self.set_title("FusionSolar-Gerätebericht")
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(True, margin=22)

    def header(self):
        self.plant_by_page[self.page] = self.current_plant
        if self.page == 1:
            return
        self.set_y(8)
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(*PETROL)
        self.cell(self.epw * 0.55, 5, "FusionSolar-Gerätebericht", align="L")
        self.set_font("DejaVu", "", 9)
        self.set_text_color(*MUTED)
        self.cell(self.epw * 0.45, 5, self.current_plant, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*PETROL)
        self.set_line_width(0.3)
        self.line(self.l_margin, 14, self.w - self.r_margin, 14)
        self.set_y(18)
        self.set_text_color(*INK)

    def footer(self):
        name = self.plant_by_page.get(self.page, self.current_plant)
        left, center, note, right = footer_parts(name, self.created, self.page, "{nb}")
        self.set_draw_color(*RULE)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.h - 16, self.w - self.r_margin, self.h - 16)
        self.set_y(-14)
        self.set_x(self.l_margin)
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(*MUTED)
        width = self.epw / 3
        self.cell(width, 4, left, align="L")
        self.cell(width, 4, center, align="C")
        self.cell(width, 4, right, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 7.5)
        self.set_text_color(120, 130, 140)
        self.cell(self.epw, 3.5, note, align="C")


def write_text(pdf: ReportPDF, text: str, size: float, bold: bool = False, color: tuple[int, int, int] = INK, line: float | None = None) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("DejaVu", "B" if bold else "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(pdf.epw, line or max(5, size * 0.45), text, new_x="LMARGIN", new_y="NEXT")


def draw_table(
    pdf: ReportPDF,
    rows: list[list[str]],
    col_widths: tuple[float, ...],
    aligns: tuple[str, ...] | str = "LEFT",
    size_pt: float = TABLE_PT,
    line_height: float = 4.8,
    padding: float = 1.4,
    gap_mm: float = 0,
) -> None:
    pdf.set_font("DejaVu", "", size_pt)
    pdf.set_text_color(*INK)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.2)
    if gap_mm:
        half = (pdf.epw - gap_mm) / 2
        label_w = half * 46 / 89
        value_w = half - label_w
        col_widths = (label_w, value_w, gap_mm, label_w, value_w)
        aligns = ("LEFT", "RIGHT", "CENTER", "LEFT", "RIGHT")
    gap_face = FontFace(fill_color=(255, 255, 255))
    with pdf.table(
        headings_style=table_headings(size_pt),
        cell_fill_color=ZEBRA,
        cell_fill_mode="ROWS",
        text_align=aligns,
        v_align="MIDDLE",
        line_height=line_height,
        padding=padding,
        col_widths=col_widths,
        repeat_headings=1,
    ) as table:
        for row in rows:
            if not gap_mm:
                table.row([str(cell) for cell in row])
                continue
            built = table.row()
            cells = [row[0], row[1], "", row[2], row[3]]
            for index, cell in enumerate(cells):
                if index == 2:
                    built.cell("", border=CellBordersLayout.NONE, style=gap_face)
                else:
                    built.cell(str(cell))
    pdf.ln(2 if size_pt < TABLE_PT else 4)
    pdf.set_x(pdf.l_margin)


def write_cover(pdf: ReportPDF, created: datetime, host: str, count: int) -> None:
    write_text(pdf, "FusionSolar-Gerätebericht", 18, bold=True, color=PETROL, line=9)
    pdf.ln(1)
    zone = created.tzname() or "Europe/Berlin"
    write_text(
        pdf,
        f"Erstellt am {created:%d.%m.%Y um %H:%M Uhr} ({zone})",
        11,
        color=MUTED,
        line=6,
    )
    write_text(pdf, f"Portal: {host}", 11, color=MUTED, line=6)
    word = "Anlage" if count == 1 else "Anlagen"
    write_text(pdf, f"{count} {word}", 11, color=INK, line=6)
    pdf.ln(1)
    y = pdf.get_y()
    pdf.set_draw_color(*PETROL)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(5)


def write_plant(pdf: ReportPDF, entry: dict) -> None:
    plant = entry["plant"]
    name = plant_heading(plant)
    write_text(pdf, f"Anlage: {name}", 14, bold=True, color=PETROL, line=7)
    y = pdf.get_y()
    pdf.set_draw_color(*PETROL)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)

    rows = plant_rows(plant)
    if rows:
        draw_table(
            pdf,
            [["Merkmal", "Wert"], *[[label, value] for label, value in rows]],
            (2, 3),
        )

    write_text(pdf, "Geräteübersicht", 12, bold=True, color=PETROL, line=6)
    pdf.ln(1)
    ordered = devices_by_name(entry["devices"])
    overview = []
    for dev in ordered:
        type_id = dev_type(dev)
        overview.append([
            device_name(dev) or "—",
            str(dev.get("esnCode") or "—"),
            TYPE_LABELS.get(type_id, f"Typ {type_id}"),
            str(dev.get("invType") or "—"),
        ])
    if overview:
        draw_table(
            pdf,
            [["Name", "Seriennummer", "Typ", "Modell"], *overview],
            (36, 36, 56, 50),
        )
    else:
        write_text(pdf, "Keine Geräte gemeldet.", 10.5, color=MUTED, line=5)
        pdf.ln(2)

    sources = entry.get("kpi_source") or {}
    kpi = entry.get("kpi") or {}
    for dev in ordered:
        type_id = dev_type(dev)
        if type_id not in PARAM_TYPES:
            continue
        dev_name = device_name(dev)
        label = TYPE_LABELS.get(type_id, f"Typ {type_id}")
        esn = str(dev.get("esnCode") or "—")
        dev_dn = str(dev.get("devDn") or "—")
        pdf.add_page()
        compact = type_id in INVERTER_TYPES
        write_text(
            pdf,
            f"{label}: {dev_name}",
            12 if compact else 14,
            bold=True,
            color=PETROL,
            line=5.5 if compact else 7,
        )
        write_text(
            pdf,
            f"Seriennummer {esn}  ·  devDn {dev_dn}",
            8 if compact else 9.5,
            color=MUTED,
            line=4 if compact else 5,
        )
        pdf.ln(0.4 if compact else 1)
        dev_id = str(dev.get("id"))
        if sources.get(dev_id) == "history":
            write_text(
                pdf,
                "Parameter aus der Historie: neuester 5-Minuten-Wert der letzten 24 Stunden.",
                9,
                color=MUTED,
                line=4.5,
            )
            pdf.ln(1)
        raw = kpi.get(dev_id, {})
        grid = inverter_parameter_grid(raw) if compact else parameter_grid(parameter_rows(raw))
        if grid:
            draw_table(
                pdf,
                [["Parameter", "Wert", "Parameter", "Wert"], *grid],
                (46, 43, 46, 43),
                ("LEFT", "RIGHT", "LEFT", "RIGHT"),
                size_pt=INVERTER_TABLE_PT if compact else TABLE_PT,
                line_height=3.5 if compact else 4.8,
                padding=0.55 if compact else 1.4,
                gap_mm=8 if compact else 0,
            )
        else:
            write_text(pdf, "Keine Parameter geliefert.", 10.5, color=MUTED, line=5)
            pdf.ln(2)


def make_pdf(model: list[dict], created: datetime, host: str) -> ReportPDF:
    pdf = ReportPDF(created)
    first = plant_heading(model[0]["plant"]) if model else ""
    pdf.current_plant = first
    pdf.add_page()
    write_cover(pdf, created, host, len(model))
    for index, entry in enumerate(model):
        name = plant_heading(entry["plant"])
        if index:
            pdf.current_plant = name
            pdf.add_page()
        write_plant(pdf, entry)
    return pdf


def render_report(model: list[dict], created: datetime, host: str) -> bytes:
    return bytes(make_pdf(model, created, host).output())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gerätebericht.py",
        description=(
            "PDF-Bericht über FusionSolar-Anlagen. Je Anlage eine Seite mit Stammdaten "
            "und Geräteübersicht, danach je Logger und Wechselrichter eine eigene Seite "
            "mit den Parametern in zwei Spalten. -h und --help zeigen diese Hilfe."
        ),
    )
    parser.add_argument(
        "-L",
        "--list-plants",
        action="store_true",
        help="Anlagen des Kontos auflisten und beenden, ohne PDF",
    )
    parser.add_argument("--plant", help="Nur diese Anlage (Name oder plantCode), Standard: alle Anlagen")
    parser.add_argument("--out", help="Ausgabedatei, Standard: data/geraetebericht-<Zeitstempel>.pdf")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> Path | None:
    args = parse_args(argv)
    host, user, code, wanted_env, gap = credentials()
    wanted = args.plant or wanted_env
    client = Client(host, user, code, gap)
    client.login()
    if args.list_plants:
        found = plants(client)
        if not found:
            raise SystemExit("Keine Anlage für dieses API-Konto.")
        print(format_plant_list(select_plants(found, wanted)))
        return None
    model = collect_report(client, wanted)
    now = datetime.now(BERLIN)
    pdf = render_report(model, now, host)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"geraetebericht-{now:%Y%m%d-%H%M%S}.pdf"
    out_path.write_bytes(pdf)
    log(f"Bericht geschrieben: {out_path} ({len(pdf)} Bytes)")
    return out_path


if __name__ == "__main__":
    try:
        run()
    except FusionSolarError as exc:
        raise SystemExit(f"FusionSolar {exc.fail_code}: {exc}") from exc
