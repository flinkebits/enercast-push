#!/usr/bin/env python3
"""PDF mit der Stringkonfiguration und den Geräteparametern aus dem Web-Portal.

Liest FusionSolar-Hist/.env. Die Anmeldung startet auf FUSIONSOLAR_HOST, die
Daten kommen von FUSIONSOLAR_WEB_HOST. FUSIONSOLAR_PLANT filtert die Anlage
(Name oder dn). Das Web-Passwort steht unter FUSIONSOLAR_WEP_PW.

Je Gerät eine eigene Seite: zuerst die festen Gerätedaten (Seriennummer,
Modell, Software, Protokoll, Modbus, Port, Garantieende), darunter für
Wechselrichter jeder String. Die Stringparameter stehen zu dritt
nebeneinander. Schutz, Isolierung und das Netzverhalten stehen einmal
je Anlage; nur abweichende Wechselrichter bekommen diese Werte erneut.
Kennlinien und laufende Sollwerte gehören nicht in diesen Bericht.
Der Seitenfuß trägt den SHA-256 der Programmdatei als Version.

Aufruf: konfigurationsbericht.py [-h] [-L] [--plant NAME_ODER_DN] [--out DATEI.pdf]
"""

from __future__ import annotations

import argparse
import base64
import codecs
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fpdf import FPDF
from fpdf.enums import CellBordersLayout
from fpdf.fonts import FontFace

BERLIN = ZoneInfo("Europe/Berlin")
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
OUT_DIR = ROOT / "data"
FONT_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

GAP_SECONDS_DEFAULT = 0.4
TABLE_PT = 10.5
PETROL = (27, 79, 114)
INK = (30, 41, 51)
MUTED = (70, 80, 90)
ZEBRA = (236, 240, 243)
RULE = (176, 190, 197)

TYPE_LABELS = {
    "Inverter": "Wechselrichter",
    "SmartLogger": "SmartLogger",
    "EMI": "Umweltsensor",
}
# Feste Gerätedaten aus query-moc-config-signal, in dieser Reihenfolge.
IDENTITY_SIGNALS = (
    (33595393, "Gerätename"),
    (50012, "Seriennummer"),
    (50009, "Modell"),
    (50010, "Softwareversion"),
    (50011, "Protokollversion"),
    (50013, "Modbus-Adresse"),
    (50019, "Geräteport"),
    (21029, "Ausgangsmodus"),
    (24001, "Stringanschluss-Erkennung"),
    (50022, "Softwarepaket"),
    (50020, "Gerätetausch"),
)
ENUM_LABELS = {
    "Three-phase four-wire system": "Dreiphasig, vier Leiter",
    "Three-phase three-wire system": "Dreiphasig, drei Leiter",
    "Disable": "aus",
    "Enable": "ein",
    "No limit": "keine Begrenzung",
    "No output": "keine Ausgabe",
    "%": "Prozent",
    "Power factor": "Leistungsfaktor",
    "Frozen active power": "eingefrorene Wirkleistung",
}
# Fest eingestellte Werte, die das Verhalten zum Netz bestimmen.
# Kennlinienpunkte, Netzcode-Details und laufende Sollwerte (prozentuale
# Wirkleistungsbegrenzung, momentanes Q/S) fehlen bewusst.
BEHAVIOR_SIGNALS = (
    ("Schutz", (
        (21032, "10-min-Überspannung Schwelle"),
        (21033, "10-min-Überspannung Zeit"),
        (230221571, "10-min-Unterspannung Schwelle"),
        (230221572, "10-min-Unterspannung Zeit"),
        (21034, "Stufe 1 Überspannung Schwelle"),
        (21035, "Stufe 1 Überspannung Zeit"),
        (21046, "Stufe 1 Unterspannung Schwelle"),
        (21047, "Stufe 1 Unterspannung Zeit"),
        (21058, "Stufe 1 Überfrequenz Schwelle"),
        (21059, "Stufe 1 Überfrequenz Zeit"),
        (21070, "Stufe 1 Unterfrequenz Schwelle"),
        (21071, "Stufe 1 Unterfrequenz Zeit"),
        (21036, "Stufe 2 Überspannung Schwelle"),
        (21037, "Stufe 2 Überspannung Zeit"),
        (21048, "Stufe 2 Unterspannung Schwelle"),
        (21049, "Stufe 2 Unterspannung Zeit"),
        (21060, "Stufe 2 Überfrequenz Schwelle"),
        (21061, "Stufe 2 Überfrequenz Zeit"),
        (21072, "Stufe 2 Unterfrequenz Schwelle"),
        (21073, "Stufe 2 Unterfrequenz Zeit"),
        (21038, "Stufe 3 Überspannung Schwelle"),
        (21039, "Stufe 3 Überspannung Zeit"),
        (21050, "Stufe 3 Unterspannung Schwelle"),
        (21051, "Stufe 3 Unterspannung Zeit"),
        (21062, "Stufe 3 Überfrequenz Schwelle"),
        (21063, "Stufe 3 Überfrequenz Zeit"),
        (21074, "Stufe 3 Unterfrequenz Schwelle"),
        (21075, "Stufe 3 Unterfrequenz Zeit"),
        (21040, "Stufe 4 Überspannung Schwelle"),
        (21041, "Stufe 4 Überspannung Zeit"),
        (21052, "Stufe 4 Unterspannung Schwelle"),
        (21053, "Stufe 4 Unterspannung Zeit"),
        (21064, "Stufe 4 Überfrequenz Schwelle"),
        (21065, "Stufe 4 Überfrequenz Zeit"),
        (21076, "Stufe 4 Unterfrequenz Schwelle"),
        (21077, "Stufe 4 Unterfrequenz Zeit"),
        (21042, "Stufe 5 Überspannung Schwelle"),
        (21043, "Stufe 5 Überspannung Zeit"),
        (21054, "Stufe 5 Unterspannung Schwelle"),
        (21055, "Stufe 5 Unterspannung Zeit"),
        (21066, "Stufe 5 Überfrequenz Schwelle"),
        (21067, "Stufe 5 Überfrequenz Zeit"),
        (21078, "Stufe 5 Unterfrequenz Schwelle"),
        (21079, "Stufe 5 Unterfrequenz Zeit"),
        (21044, "Stufe 6 Überspannung Schwelle"),
        (21045, "Stufe 6 Überspannung Zeit"),
        (21056, "Stufe 6 Unterspannung Schwelle"),
        (21057, "Stufe 6 Unterspannung Zeit"),
        (21068, "Stufe 6 Überfrequenz Schwelle"),
        (21069, "Stufe 6 Überfrequenz Zeit"),
        (21080, "Stufe 6 Unterfrequenz Schwelle"),
        (21081, "Stufe 6 Unterfrequenz Zeit"),
        (230222164, "Frequenzänderungsrate"),
        (230222165, "Frequenzänderungsrate Schwelle"),
        (230222166, "Frequenzänderungsrate Zeit"),
        (230223019, "Frequenzänderungsrate Zeit 2"),
        (230222115, "Aktive Inselnetzerkennung"),
        (230222116, "Passive Inselnetzerkennung"),
        (230221520, "Phasenwinkelschutz"),
        (230221519, "Phasenwinkelschutz Schwelle"),
        (230221518, "Spannungsunsymmetrie Schwelle"),
        (230224172, "RCD-Auslöseschwelle"),
        (230222175, "RCD-Stromgrenze"),
        (230222073, "RCD-Erweiterung"),
    )),
    ("Isolierung", (
        (21096, "Isolationswiderstand Schwelle"),
        (230222096, "Abschaltung bei Erdschluss"),
        (230222227, "Erdschlusserkennung"),
        (230222224, "Wiedereinschaltung nach String-Erdschluss"),
    )),
    ("Leistungsregelung", (
        (21115, "Wirkleistungsmodus"),
        (21099, "Max. Wirkleistung"),
        (230222677, "Max. Scheinleistung"),
        (230222680, "Wirkleistungs-Grundwert"),
        (230222679, "Scheinleistungs-Grundwert"),
        (21022, "Feste Wirkleistungsbegrenzung"),
        (21116, "Max. Einspeiseleistung %"),
        (21098, "Max. Einspeiseleistung kW"),
        (230222675, "Fernwirkungsfahrplan"),
        (230222676, "Gültigkeit der Fahrplanvorgabe"),
        (230222685, "Wirkleistungsgradient"),
        (230222689, "Blindleistungsgradient"),
        (230222682, "Anlagengradient Wirkleistung"),
        (230223888, "Blindleistungsmodus"),
        (21023, "Verschiebungsfaktor"),
        (230222681, "Abschaltung bei 0 % Leistungsgrenze"),
        (230222090, "Sanftanlaufzeit"),
        (230222091, "Abschaltgradient"),
        (230224082, "DRM"),
        (230225114, "Einspeisung bei begrenztem Strom"),
    )),
    ("Frequenzabhängige Wirkleistung", (
        (230222695, "LFSM-O"),
        (21103, "LFSM-O Auslösefrequenz"),
        (21104, "LFSM-O Rückkehrfrequenz"),
        (21102, "LFSM-O zweite Frequenz"),
        (230222769, "LFSM-O zweiter Leistungspunkt"),
        (230222772, "LFSM-O Rückkehrgradient"),
        (230222219, "Überfrequenz-Reduktion Verzögerung"),
        (230222220, "Überfrequenz-Reduktion Hysterese"),
        (22114, "Bezugsleistung Überfrequenz-Reduktion"),
        (230222775, "Übergangsfrequenz der Frequenzreduktion"),
        (230222776, "LFSM-U"),
        (230222216, "LFSM-U Auslösefrequenz"),
        (230222217, "LFSM-U Rückkehrfrequenz"),
        (230222214, "LFSM-U zweite Frequenz"),
        (230222215, "LFSM-U zweiter Leistungspunkt"),
        (230222213, "LFSM-U Rückkehrgradient"),
    )),
    ("Kommunikationsausfall", (
        (230222189, "Wirkleistungsmodus"),
        (230222190, "Wirkleistungsschwelle %"),
        (230222191, "Wirkleistungsschwelle kW"),
        (230222193, "Blindleistungsmodus"),
        (230222194, "Verschiebungsfaktor"),
        (230222195, "Blindleistung Q/S"),
        (230222168, "Fail-Safe bei Verbindungsabbruch"),
        (230222169, "Fail-Safe-Wirkleistungsgrenze"),
        (230222170, "Schutzzeit bei Verbindungsabbruch"),
        (230222087, "Abschaltung bei Unterbrechung"),
        (230222089, "Abschaltdauer"),
    )),
)
CONFIG_SIGNAL_IDS = ",".join(
    str(signal_id)
    for signal_id in dict.fromkeys(
        [signal_id for signal_id, _label in IDENTITY_SIGNALS]
        + [signal_id for _group, items in BEHAVIOR_SIGNALS for signal_id, _label in items]
    )
)
# Vom Portal bestätigt: Modul JAM66D45-615/LB-SF trägt moduleType 5 als „Typ N zweiseitig“.
MODULE_TYPE_LABELS = {"5": "Typ N zweiseitig"}
# Reihenfolge wie in der aufgeklappten Stringdetail-Ansicht des Portals.
# Kurzbezeichnungen, damit drei Paare je Zeile ohne Umbruch passen.
STRING_FIELDS = (
    ("manufacturer", "Modulhersteller", ""),
    ("moduleVersion", "Modultyp", ""),
    ("moduleType", "Zelltyp", ""),
    ("standardPower", "Pmax", "W"),
    ("modulesNumPerString", "Module", ""),
    ("fixedPower", "String-Leistung", "Wp"),
    ("maxPowerPointVoltage", "Vmp", "V"),
    ("maxPowerPointCurrent", "Imp", "A"),
    ("componentsNominalVoltage", "Voc", "V"),
    ("nominalCurrentComponent", "Isc", "A"),
    ("maxPowerTempCoef", "TK Pmax", "%/°C"),
    ("voltageTempCoef", "TK Voc", "%/°C"),
    ("currentTempCoef", "TK Isc", "%/°C"),
    ("cellsNumPerModule", "Zellen", "Stück/Modul"),
    ("moduleProductionDate", "Installation", ""),
    ("firstDegradationDrate", "Deg. 1. Jahr", "%/Jahr"),
    ("secondDegradationDrate", "Deg. ab 2. Jahr", "%/Jahr"),
    ("fillFactor", "Füllfaktor", "%"),
    ("moduleRatio", "Wirkungsgrad", "%"),
    ("minWorkTemp", "T min", "°C"),
    ("maxWorkTemp", "T max", "°C"),
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


class WebSession:
    """Browsersitzung: Login auf dem Northbound-Host, Daten auf dem Web-Host."""

    def __init__(self, login_host: str, web_host: str, user: str, password: str, gap_seconds: float):
        self.login_base = "https://" + login_host.strip().removeprefix("https://").removeprefix("http://").strip("/")
        self.web_base = "https://" + web_host.strip().removeprefix("https://").removeprefix("http://").strip("/")
        self.user = user
        self.password = password
        self.gap_seconds = gap_seconds
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._last = 0.0
        self.roarand = ""

    def login(self) -> None:
        self._open(self.login_base + "/unisso/login.action")
        _status, _headers, raw = self._open(self.login_base + "/unisso/pubkey")
        pub = json.loads(raw)
        key = serialization.load_pem_public_key(pub["pubKey"].encode())
        encrypted = base64.b64encode(key.encrypt(
            self.password.encode(),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA384()),
                algorithm=hashes.SHA384(),
                label=None,
            ),
        )).decode()
        nonce = codecs.encode(encrypted[:16].encode("ascii"), "hex").decode()
        query = urlencode({
            "service": "/unisess/v1/auth?service=%2Fnetecowebext%2Fhome%2Findex.html",
            "timeStamp": pub["timeStamp"],
            "nonce": nonce,
        })
        _status, _headers, raw = self._open(
            self.login_base + "/unisso/v3/validateUser.action?" + query,
            {"organizationName": "", "username": self.user, "password": encrypted + str(pub.get("version") or "")},
        )
        body = json.loads(raw)
        if str(body.get("errorCode")) != "470":
            raise FusionSolarError(body.get("errorCode"), body.get("errorMsg") or "Web-Login abgelehnt.")
        regions = body.get("respMultiRegionName") or []
        ticket = self.login_base + regions[1]
        for _hop in range(4):
            status, headers, _raw = self._open(ticket)
            location = headers.get("Location")
            if status < 300 or status >= 400 or not location:
                break
            ticket = urljoin(ticket, location)
        _status, _headers, raw = self._open(self.web_base + "/rest/dpcloud/auth/v1/keep-alive")
        alive = json.loads(raw)
        token = alive.get("payload") if isinstance(alive, dict) else None
        if not token:
            raise FusionSolarError(alive.get("code") if isinstance(alive, dict) else 0, "Web-Sitzung ohne Token.")
        self.roarand = token

    def stations(self) -> list[dict]:
        body = self._json("/rest/pvms/web/station/v1/station/station-list", {
            "curPage": 1,
            "pageSize": 100,
            "gridConnectedTime": "",
            "queryTime": int(time.time() * 1000),
            "timeZone": 1,
            "sortId": "createTime",
            "sortDir": "DESC",
            "locale": "de_DE",
        })
        data = body.get("data") or {}
        return list(data.get("list") or [])

    def devices(self, plant_dn: str) -> list[dict]:
        page_size = 100
        pages = []
        total = None
        for page in range(1, 21):
            body = self._json(device_list_path(plant_dn, page, page_size))
            batch = [item for item in (body.get("data") or []) if isinstance(item, dict)]
            if total is None:
                try:
                    total = int(body.get("total"))
                except (TypeError, ValueError):
                    total = None
            pages.append(batch)
            found = merge_device_pages(pages)
            previous = merge_device_pages(pages[:-1])
            if not batch or len(found) == len(previous):
                break
            if total is not None and len(found) >= total:
                break
            if len(batch) < page_size:
                break
        return merge_device_pages(pages)

    def config_signals(self, dn: str) -> list[dict]:
        body = self._json(
            "/rest/neteco/web/config/device/v1/config/query-moc-config-signal"
            f"?dn={quote(dn)}&signals={CONFIG_SIGNAL_IDS}",
        )
        data = body.get("data") if isinstance(body, dict) else None
        return list(data or [])

    def string_config(self, dn: str) -> list[dict]:
        body = self._json(
            "/rest/pvms/web/ivcurve/v1/devmanager/originaloptions?dnsList=" + quote(dn),
        )
        data = body.get("data") or {}
        modules = data.get("currentModule") or []
        return sorted(modules, key=lambda item: int(item.get("pvIndex") or 0))

    def _json(self, path: str, payload: dict | None = None) -> dict:
        status, _headers, raw = self._open(self.web_base + path, payload, web=True)
        if status != 200:
            raise FusionSolarError(status, f"{path.split('?', 1)[0]} HTTP {status}")
        body = json.loads(raw) if raw else {}
        if isinstance(body, dict) and body.get("success") is False:
            raise FusionSolarError(body.get("failCode") or status, str(body.get("message") or "Abfrage fehlgeschlagen."))
        return body

    def _open(self, url: str, payload: dict | None = None, web: bool = False):
        self._gap()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "de-DE,de;q=0.9",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if web and self.roarand:
            headers["roarand"] = self.roarand
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=90) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _gap(self) -> None:
        wait = self.gap_seconds - (time.monotonic() - self._last)
        if self._last and wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def credentials() -> dict[str, str | float]:
    env = load_env(ENV_PATH)
    host = env.get("FUSIONSOLAR_HOST", "")
    web_host = env.get("FUSIONSOLAR_WEB_HOST", "")
    user = env.get("FUSIONSOLAR_WEB_USER", "")
    password = env.get("FUSIONSOLAR_WEP_PW") or env.get("FUSIONSOLAR_WEB_PW") or ""
    missing = [
        name
        for name, value in (
            ("FUSIONSOLAR_HOST", host),
            ("FUSIONSOLAR_WEB_HOST", web_host),
            ("FUSIONSOLAR_WEB_USER", user),
            ("FUSIONSOLAR_WEP_PW", password),
        )
        if not value
    ]
    if missing:
        raise SystemExit("In FusionSolar-Hist/.env fehlt: " + ", ".join(missing))
    try:
        gap = float(env.get("FUSIONSOLAR_GAP_SECONDS", GAP_SECONDS_DEFAULT))
    except ValueError:
        gap = GAP_SECONDS_DEFAULT
    return {
        "host": host,
        "web_host": web_host,
        "user": user,
        "password": password,
        "plant": env.get("FUSIONSOLAR_PLANT", ""),
        "gap": gap,
    }


def plant_name(plant: dict) -> str:
    return str(plant.get("name") or plant.get("stationName") or plant.get("dn") or "Anlage")


def select_plants(found: list[dict], wanted: str) -> list[dict]:
    if not wanted:
        return found
    folded = wanted.casefold()
    matches = [
        plant
        for plant in found
        if folded in plant_name(plant).casefold() or folded in str(plant.get("dn") or "").casefold()
    ]
    if not matches:
        names = ", ".join(f"{plant_name(plant)} ({plant.get('dn')})" for plant in found)
        raise SystemExit(f"Anlage {wanted!r} nicht gefunden. Sichtbar: {names}")
    return matches


def device_name(dev: dict) -> str:
    return str(dev.get("name") or dev.get("dn") or "")


def device_list_path(plant_dn: str, page: int, page_size: int = 100) -> str:
    """Geräteliste seitenweise. pageSize ohne conditionParams-Präfix wird ignoriert."""
    query = urlencode({
        "conditionParams.parentDn": plant_dn,
        "conditionParams.recordperpage": page_size,
        "conditionParams.pageNo": page,
    })
    return "/rest/neteco/web/config/device/v1/device-list?" + query


def merge_device_pages(pages: list[list[dict]]) -> list[dict]:
    found = []
    seen = set()
    for batch in pages:
        for item in batch:
            key = str(item.get("dn") or item.get("name") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(item)
    return found


def devices_by_name(dev_list: list[dict]) -> list[dict]:
    return sorted(dev_list, key=lambda dev: device_name(dev).casefold())


def format_number(value) -> str:
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


def format_epoch(value) -> str:
    number = int(float(value))
    if number > 10_000_000_000:
        number = number / 1000
    return datetime.fromtimestamp(number, BERLIN).strftime("%Y-%m-%d")


def blank(value) -> bool:
    return value is None or str(value).strip() in ("", "-", "—", "null")


def with_unit(text: str, unit: str) -> str:
    if text == "—" or not unit:
        return text
    return f"{text} {unit}"


def font_safe(text: str) -> bool:
    """DejaVu Sans enthält kein CJK. Solche Bezeichnungen lässt der Bericht weg."""
    return all(ord(ch) < 0x2E80 for ch in text)


def enum_text(signal: dict, value) -> str | None:
    mapping = signal.get("enumList") or signal.get("enumMap") or {}
    if isinstance(mapping, dict):
        label = mapping.get(str(value))
        if isinstance(label, str) and label.strip():
            return ENUM_LABELS.get(label, label)
    return None


def warranty_text(device: dict | None) -> str | None:
    """Garantieende kommt aus der Geräteliste, nicht aus den Konfigurationssignalen."""
    if not device:
        return None
    value = device.get("warrantExpDeadlinedateStr")
    if blank(value):
        return None
    text = str(value).strip()
    if text.startswith("1970-") or not font_safe(text):
        return None
    return text


def device_parameter_rows(signals: list[dict], device: dict | None = None) -> list[tuple[str, str]]:
    """Feste Gerätedaten mit belegtem Wert. Leere Felder und Momentanwerte fehlen."""
    by_id = {}
    for signal in signals:
        try:
            by_id[int(signal.get("id"))] = signal
        except (TypeError, ValueError):
            continue
    rows = []
    for signal_id, label in IDENTITY_SIGNALS:
        signal = by_id.get(signal_id)
        if signal:
            value = signal.get("value")
            if not blank(value):
                text = enum_text(signal, value) or format_number(value)
                if font_safe(label) and font_safe(text):
                    rows.append((label, text))
        if signal_id == 50019:
            warranty = warranty_text(device)
            if warranty:
                rows.append(("Garantieende", warranty))
    return rows


def setting_text(signal: dict) -> str | None:
    value = signal.get("value")
    if blank(value):
        return None
    text = enum_text(signal, value) or ENUM_LABELS.get(str(value), format_number(value))
    unit = str(signal.get("unit") or "").strip()
    if unit and unit not in text:
        text = f"{text} {unit}"
    if not font_safe(text):
        return None
    return text


def filled_behavior(signals: list[dict]) -> dict[int, tuple[str, str, str]]:
    """Signal-ID auf (Gruppe, Bezeichnung, Wert) für belegte Netzverhaltensparameter."""
    by_id = {}
    for signal in signals:
        try:
            by_id[int(signal.get("id"))] = signal
        except (TypeError, ValueError):
            continue
    found = {}
    for group, items in BEHAVIOR_SIGNALS:
        for signal_id, label in items:
            signal = by_id.get(signal_id)
            if not signal:
                continue
            text = setting_text(signal)
            if text:
                found[signal_id] = (group, label, text)
    return found


def device_list_text(names: list[str]) -> str:
    ordered = sorted(names, key=str.casefold)
    if len(ordered) <= 3:
        return ", ".join(ordered)
    return f"{len(ordered)} Wechselrichter"


def summarize_behavior(devices: list[tuple[str, list[dict]]]) -> dict:
    """Gemeinsame Netzverhaltenswerte und Abweichungen je Wechselrichter.

    Ein Parameter gilt nur dann als gemeinsam, wenn jeder Wechselrichter denselben
    belegten Wert liefert. Sonst ist der häufigste Wert die Referenz.
    """
    filled = [(name, filled_behavior(signals)) for name, signals in devices]
    sections: dict[str, list[tuple[str, str]]] = {}
    deviations = []
    per_device: dict[str, list[tuple[str, str, str]]] = {}
    for group, items in BEHAVIOR_SIGNALS:
        for signal_id, label in items:
            present = [(name, values[signal_id][2]) for name, values in filled if signal_id in values]
            if not present:
                continue
            missing = [name for name, values in filled if signal_id not in values]
            texts = {text for _name, text in present}
            if len(texts) == 1 and not missing:
                sections.setdefault(group, []).append((label, present[0][1]))
                continue
            grouped: dict[str, list[str]] = {}
            for name, text in present:
                grouped.setdefault(text, []).append(name)
            if missing:
                grouped.setdefault("—", []).extend(missing)
            reference = min(grouped, key=lambda value: (-len(grouped[value]), value == "—", value))
            ordered = sorted(grouped, key=lambda value: (-len(grouped[value]), value == "—", value))
            deviations.append((label, [(value, device_list_text(grouped[value])) for value in ordered]))
            for name, text in present:
                if text != reference:
                    per_device.setdefault(name, []).append((label, text, reference))
            for name in missing:
                per_device.setdefault(name, []).append((label, "—", reference))
    return {
        "inverter_count": len(devices),
        "sections": [(group, sections[group]) for group, _items in BEHAVIOR_SIGNALS if group in sections],
        "deviations": deviations,
        "per_device": per_device,
    }


def string_parameter_rows(module: dict) -> list[tuple[str, str]]:
    rows = []
    for key, label, unit in STRING_FIELDS:
        value = module.get(key)
        if key == "moduleType" and not blank(value):
            text = MODULE_TYPE_LABELS.get(str(value), str(value))
        elif key == "moduleProductionDate" and not blank(value):
            try:
                text = format_epoch(value)
            except (TypeError, ValueError, OSError):
                text = str(value)
        elif blank(value):
            text = "—"
        else:
            text = format_number(value)
        rows.append((label, with_unit(text, unit)))
    return rows


def triple_rows(rows: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Je Zeile drei Bezeichnung-Wert-Paare. Die letzte Zeile wird aufgefüllt."""
    grid = []
    for index in range(0, len(rows), 3):
        chunk = list(rows[index:index + 3])
        while len(chunk) < 3:
            chunk.append(("", ""))
        grid.append(chunk)
    return grid


def format_plant_list(found: list[dict]) -> str:
    count = len(found)
    word = "Anlage" if count == 1 else "Anlagen"
    lines = [f"{count} {word}"]
    for plant in found:
        lines.append(f"{plant_name(plant)}  {plant.get('dn') or '—'}")
    return "\n".join(lines)


def collect_report(session: WebSession, wanted: str) -> list[dict]:
    found = session.stations()
    if not found:
        raise SystemExit("Keine Anlage für dieses Web-Konto.")
    selected = select_plants(found, wanted)
    report = []
    for plant in selected:
        dev_list = devices_by_name(session.devices(str(plant.get("dn") or "")))
        devices = []
        for dev in dev_list:
            name = device_name(dev)
            try:
                signals = session.config_signals(str(dev.get("dn") or ""))
            except FusionSolarError as exc:
                log(f"{name}: Geräteparameter {exc.fail_code}: {exc}")
                signals = []
            strings = []
            if dev.get("mocTypeName") == "Inverter" and dev.get("dn"):
                try:
                    strings = session.string_config(str(dev["dn"]))
                except FusionSolarError as exc:
                    log(f"{name}: Stringkonfiguration {exc.fail_code}: {exc}")
            devices.append({"device": dev, "signals": signals, "strings": strings})
        report.append({"plant": plant, "devices": devices})
        log(f"Anlage {plant_name(plant)} ({plant.get('dn')}): {len(devices)} Geräte")
    return report


def source_version(path: Path | None = None) -> str:
    """SHA-256 der Programmdatei. Der Wert kennzeichnet genau diese Quellversion."""
    target = Path(__file__) if path is None else path
    return hashlib.sha256(target.read_bytes()).hexdigest()


def footer_parts(
    plant_name_text: str,
    created: datetime,
    page: int,
    total: int | str,
    version: str,
) -> tuple[str, str, str, str, str]:
    left = plant_name_text or "—"
    center = f"Erstellt am {created:%d.%m.%Y, %H:%M} Uhr"
    note = "Gerätedaten, Netzverhalten und Stringkonfiguration"
    right = f"Seite {page} von {total}"
    identity = f"Erstellt mit konfigurationsbericht.py SHA-256: {version}"
    return left, center, note, right, identity


class ReportPDF(FPDF):
    def __init__(self, created: datetime, version: str | None = None):
        super().__init__(orientation="P", unit="mm", format="A4")
        if not FONT_REGULAR.is_file() or not FONT_BOLD.is_file():
            raise SystemExit("DejaVu Sans fehlt. Paket fonts-dejavu-core installieren.")
        self.add_font("DejaVu", "", str(FONT_REGULAR))
        self.add_font("DejaVu", "B", str(FONT_BOLD))
        self.created = created
        self.current_plant = ""
        self.plant_by_page: dict[int, str] = {}
        self.set_title("FusionSolar-Konfigurationsbericht")
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(True, margin=24)
        self.version = source_version() if version is None else version

    def header(self):
        self.plant_by_page[self.page] = self.current_plant
        if self.page == 1:
            return
        self.set_y(8)
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(*PETROL)
        self.cell(self.epw * 0.55, 5, "FusionSolar-Konfigurationsbericht", align="L")
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
        left, center, note, right, identity = footer_parts(name, self.created, self.page, "{nb}", self.version)
        self.set_draw_color(*RULE)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.h - 20, self.w - self.r_margin, self.h - 20)
        self.set_y(-18)
        self.set_x(self.l_margin)
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(*MUTED)
        width = self.epw / 3
        self.cell(width, 3.8, left, align="L")
        self.cell(width, 3.8, center, align="C")
        self.cell(width, 3.8, right, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 7.5)
        self.set_text_color(120, 130, 140)
        self.cell(self.epw, 3.3, note, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_x(self.l_margin)
        self.set_font("DejaVu", "", 6.5)
        self.cell(self.epw, 3.2, identity, align="C")


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


def write_settings(pdf: ReportPDF, rows: list[tuple[str, str]]) -> None:
    draw_table(
        pdf,
        [["Angabe", "Wert"], *[[label, value] for label, value in rows]],
        (3, 2),
        ("LEFT", "LEFT"),
        size_pt=9,
        line_height=4.0,
        padding=0.9,
    )


def write_behavior(pdf: ReportPDF, summary: dict) -> None:
    count = summary["inverter_count"]
    if count == 0 or (not summary["sections"] and not summary["deviations"]):
        return
    pdf.add_page()
    write_text(pdf, "Netzverhalten", 14, bold=True, color=PETROL, line=7)
    if count == 1:
        lead = "Werte des Wechselrichters. Kennlinien sind nicht enthalten."
    else:
        lead = (
            f"Werte, die bei allen {count} Wechselrichtern gleich sind. "
            "Kennlinien sind nicht enthalten."
        )
    write_text(pdf, lead, 10, color=MUTED, line=5)
    pdf.ln(1)
    for group, rows in summary["sections"]:
        write_text(pdf, group, 11, bold=True, color=PETROL, line=5)
        pdf.ln(0.3)
        write_settings(pdf, rows)
    if not summary["deviations"]:
        return
    write_text(pdf, "Abweichungen", 12, bold=True, color=PETROL, line=6)
    write_text(
        pdf,
        "Diese Parameter sind nicht bei allen Wechselrichtern gleich. Referenz ist der häufigste Wert.",
        10,
        color=MUTED,
        line=5,
    )
    pdf.ln(0.4)
    deviation_rows = []
    for label, groups in summary["deviations"]:
        for value, names in groups:
            deviation_rows.append([label, value, names])
    draw_table(
        pdf,
        [["Parameter", "Wert", "Wechselrichter"], *deviation_rows],
        (46, 28, 36),
        ("LEFT", "LEFT", "LEFT"),
        size_pt=8.5,
        line_height=3.8,
        padding=0.8,
    )


def write_device_behavior(pdf: ReportPDF, name: str, summary: dict) -> None:
    if summary["inverter_count"] == 0:
        return
    if not summary["sections"] and not summary["deviations"]:
        return
    own = summary["per_device"].get(name) or []
    if not own:
        write_text(pdf, "Netzverhalten identisch mit der Anlagenreferenz.", 10, color=MUTED, line=5)
        pdf.ln(1)
        return
    write_text(pdf, "Abweichendes Netzverhalten", 11, bold=True, color=PETROL, line=5)
    pdf.ln(0.3)
    draw_table(
        pdf,
        [["Parameter", "dieser WR", "Referenz"], *[[label, value, reference] for label, value, reference in own]],
        (50, 30, 30),
        ("LEFT", "LEFT", "LEFT"),
        size_pt=9,
        line_height=4.0,
        padding=0.9,
    )


def write_identity(pdf: ReportPDF, rows: list[tuple[str, str]]) -> None:
    draw_table(
        pdf,
        [["Angabe", "Wert"], *[[label, value] for label, value in rows]],
        (2, 3),
        ("LEFT", "LEFT"),
        size_pt=TABLE_PT,
        line_height=4.6,
        padding=1.1,
    )


def write_string_grid(pdf: ReportPDF, rows: list[tuple[str, str]]) -> None:
    """Drei Bezeichnung-Wert-Paare je Zeile, dazwischen ein doppelter Trennstrich."""
    groups = triple_rows(rows)
    if not groups:
        return
    rule_w = 1.15
    gap_w = 2.0
    value_w = 31.0
    pair_w = (pdf.epw - 2 * gap_w - 3 * rule_w) / 3
    label_w = pair_w - value_w
    widths = (
        label_w, rule_w, value_w, gap_w,
        label_w, rule_w, value_w, gap_w,
        label_w, rule_w, value_w,
    )
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(*INK)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.25)
    plain = FontFace(fill_color=(255, 255, 255))
    zebra = FontFace(fill_color=ZEBRA)
    side = CellBordersLayout.LEFT | CellBordersLayout.RIGHT | CellBordersLayout.TOP | CellBordersLayout.BOTTOM
    with pdf.table(
        borders_layout="NONE",
        first_row_as_headings=False,
        num_heading_rows=0,
        v_align="MIDDLE",
        line_height=3.3,
        padding=0.45,
        col_widths=widths,
    ) as table:
        for row_index, group in enumerate(groups):
            face = zebra if row_index % 2 == 0 else plain
            built = table.row()
            for pair_index, (label, value) in enumerate(group):
                filled = bool(label or value)
                if filled:
                    built.cell(
                        label,
                        align="LEFT",
                        style=face,
                        border=CellBordersLayout.LEFT | CellBordersLayout.TOP | CellBordersLayout.BOTTOM,
                    )
                    built.cell("", style=plain, border=side)
                    built.cell(
                        value,
                        align="RIGHT",
                        style=face,
                        border=CellBordersLayout.TOP | CellBordersLayout.BOTTOM | CellBordersLayout.RIGHT,
                    )
                else:
                    for _cell in range(3):
                        built.cell("", style=plain, border=CellBordersLayout.NONE)
                if pair_index < 2:
                    built.cell("", style=plain, border=CellBordersLayout.NONE)
    pdf.ln(1.2)
    pdf.set_x(pdf.l_margin)


def write_cover(pdf: ReportPDF, created: datetime, host: str, count: int) -> None:
    write_text(pdf, "FusionSolar-Konfigurationsbericht", 18, bold=True, color=PETROL, line=9)
    pdf.ln(1)
    zone = created.tzname() or "Europe/Berlin"
    write_text(pdf, f"Erstellt am {created:%d.%m.%Y um %H:%M Uhr} ({zone})", 11, color=MUTED, line=6)
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
    name = plant_name(plant)
    write_text(pdf, f"Anlage: {name}", 14, bold=True, color=PETROL, line=7)
    y = pdf.get_y()
    pdf.set_draw_color(*PETROL)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    draw_table(
        pdf,
        [["Merkmal", "Wert"], ["Name", name], ["Anlagencode", str(plant.get("dn") or "—")]],
        (2, 3),
    )
    write_text(pdf, "Geräteübersicht", 12, bold=True, color=PETROL, line=6)
    pdf.ln(1)
    overview = []
    for item in entry["devices"]:
        dev = item["device"]
        kind = str(dev.get("mocTypeName") or "")
        overview.append([
            device_name(dev) or "—",
            TYPE_LABELS.get(kind, kind or "—"),
            str(len(item.get("strings") or [])),
        ])
    if overview:
        draw_table(pdf, [["Name", "Typ", "Strings"], *overview], (70, 60, 30))
    inverters = [
        (device_name(item["device"]), item.get("signals") or [])
        for item in entry["devices"]
        if item["device"].get("mocTypeName") == "Inverter"
    ]
    behavior = summarize_behavior(inverters)
    write_behavior(pdf, behavior)
    for item in entry["devices"]:
        dev = item["device"]
        kind = str(dev.get("mocTypeName") or "")
        pdf.add_page()
        write_text(
            pdf,
            f"{TYPE_LABELS.get(kind, kind or 'Gerät')}: {device_name(dev)}",
            12,
            bold=True,
            color=PETROL,
            line=5.5,
        )
        pdf.ln(0.6)
        write_text(pdf, "Geräteparameter", 11, bold=True, color=PETROL, line=5)
        pdf.ln(0.4)
        device_rows = device_parameter_rows(item.get("signals") or [], dev)
        if device_rows:
            write_identity(pdf, device_rows)
        else:
            write_text(pdf, "Keine festen Gerätedaten geliefert.", 10, color=MUTED, line=5)
            pdf.ln(2)
        if kind == "Inverter":
            write_device_behavior(pdf, device_name(dev), behavior)
        strings = item.get("strings") or []
        if not strings:
            continue
        write_text(pdf, "Stringdetails", 11, bold=True, color=PETROL, line=5)
        for module in strings:
            number = module.get("pvIndex") or "?"
            write_text(pdf, f"PV{number}", 10, bold=True, color=INK, line=4.5)
            pdf.ln(0.15)
            write_string_grid(pdf, string_parameter_rows(module))


def make_pdf(model: list[dict], created: datetime, host: str) -> ReportPDF:
    pdf = ReportPDF(created)
    first = plant_name(model[0]["plant"]) if model else ""
    pdf.current_plant = first
    pdf.add_page()
    write_cover(pdf, created, host, len(model))
    for index, entry in enumerate(model):
        name = plant_name(entry["plant"])
        if index:
            pdf.current_plant = name
            pdf.add_page()
        else:
            pdf.current_plant = name
        write_plant(pdf, entry)
    return pdf


def render_report(model: list[dict], created: datetime, host: str) -> bytes:
    return bytes(make_pdf(model, created, host).output())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="konfigurationsbericht.py",
        description=(
            "PDF der Stringkonfiguration. Je Gerät zuerst Seriennummer, Modell und "
            "weitere feste Angaben, danach je String die Modulparameter in drei Spalten. "
            "-h und --help zeigen diese Hilfe."
        ),
    )
    parser.add_argument("-L", "--list-plants", action="store_true", help="Anlagen auflisten und beenden, ohne PDF")
    parser.add_argument("--plant", help="Nur diese Anlage (Name oder dn), Standard aus FUSIONSOLAR_PLANT oder alle")
    parser.add_argument("--out", help="Ausgabedatei, Standard: data/konfigurationsbericht-<Zeitstempel>.pdf")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> Path | None:
    args = parse_args(argv)
    env = credentials()
    wanted = args.plant or str(env["plant"])
    session = WebSession(
        str(env["host"]),
        str(env["web_host"]),
        str(env["user"]),
        str(env["password"]),
        float(env["gap"]),
    )
    session.login()
    if args.list_plants:
        found = session.stations()
        if not found:
            raise SystemExit("Keine Anlage für dieses Web-Konto.")
        print(format_plant_list(select_plants(found, wanted)))
        return None
    model = collect_report(session, wanted)
    now = datetime.now(BERLIN)
    pdf = render_report(model, now, str(env["web_host"]))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"konfigurationsbericht-{now:%Y%m%d-%H%M%S}.pdf"
    out_path.write_bytes(pdf)
    log(f"Bericht geschrieben: {out_path} ({len(pdf)} Bytes)")
    return out_path


if __name__ == "__main__":
    try:
        run()
    except FusionSolarError as exc:
        raise SystemExit(f"FusionSolar {exc.fail_code}: {exc}") from exc
