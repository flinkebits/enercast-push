"""Live-Wirkleistung und PV-Abregelung an die enercast-API senden."""

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://rest-api.enercast.de"
PORTFOLIO_ID = "default"
ENV_PATH = Path(__file__).with_name(".env")
TOLERANCE = 1.05


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def load_env(path):
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


def to_watts(power, power_unit):
    if power_unit == "W":
        factor = 1
    elif power_unit == "kW":
        factor = 1000
    else:
        raise ValueError("power_unit muss W oder kW sein.")
    if isinstance(power, bool) or not isinstance(power, (int, float)):
        raise ValueError("Leistung muss eine Zahl sein.")
    watts = float(power) * factor
    if not math.isfinite(watts):
        raise ValueError("Leistung muss endlich sein.")
    return watts


def ceiling_watts(asset):
    limits = []
    for key in ("rated-power", "net-active-power"):
        value = asset.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(value):
            limits.append(float(value))
    if not limits:
        return None
    return max(limits) * TOLERANCE


def require_within(name, watts, ceiling):
    if isinstance(watts, bool) or not isinstance(watts, (int, float)) or not math.isfinite(watts):
        raise ValueError(f"{name} muss endlich sein.")
    if watts < 0:
        raise ValueError(f"{name} muss >= 0 sein.")
    if ceiling is not None and watts > ceiling:
        raise ValueError(f"{name} liegt über der Anlagenleistung.")


def require_period(period_minutes):
    if (
        isinstance(period_minutes, bool)
        or not isinstance(period_minutes, int)
        or period_minutes < 1
        or 60 % period_minutes != 0
    ):
        raise ValueError("period_minutes muss 60 teilen.")
    return period_minutes


def period_token(period_minutes):
    if period_minutes == 60:
        return "PT1H"
    return f"PT{period_minutes}M"


def parse_start(start, now, period_minutes=1):
    period_minutes = require_period(period_minutes)
    if start is None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
        floored_minute = (now.minute // period_minutes) * period_minutes
        floored = now.replace(minute=floored_minute)
        return floored - timedelta(minutes=period_minutes)

    if isinstance(start, datetime):
        parsed = start
    elif isinstance(start, str):
        text = start.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("start ist kein UTC-Zeitstempel.") from exc
    else:
        raise ValueError("start ist kein UTC-Zeitstempel.")

    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("start muss UTC sein.")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.second or parsed.microsecond or parsed.minute % period_minutes:
        if period_minutes == 1:
            raise ValueError("start muss auf einer vollen Minute liegen.")
        raise ValueError(f"start muss auf einem {period_minutes}-Minuten-Raster liegen.")
    return parsed.replace(second=0, microsecond=0)


def format_instant(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveData:
    def __init__(self, username, password, account_id=None):
        self.username = username
        self.password = password
        self.account_id = account_id or username

    @classmethod
    def from_env(cls, path=None):
        env = load_env(Path(path) if path else ENV_PATH)
        username = env.get("ENERCAST_USERNAME")
        password = env.get("ENERCAST_PASSWORD")
        if not username or not password:
            raise ValueError(
                "ENERCAST_USERNAME und ENERCAST_PASSWORD müssen in der .env stehen."
            )
        return cls(username, password, env.get("ENERCAST_ACCOUNT_ID") or username)

    def send(self, plant_key, power, curtailment_w, power_unit="W", start=None, period_minutes=1):
        key = plant_key.strip() if isinstance(plant_key, str) else ""
        if not key:
            raise ValueError("Anlagenschlüssel fehlt.")
        period_minutes = require_period(period_minutes)
        power_w = to_watts(power, power_unit)
        require_within("Leistung", power_w, None)
        require_within("Abregelung", curtailment_w, None)
        start_at = parse_start(start, datetime.now(timezone.utc), period_minutes)

        token = self._login()
        asset = self._get_asset(token, key)
        ceiling = ceiling_watts(asset)
        require_within("Leistung", power_w, ceiling)
        require_within("Abregelung", curtailment_w, ceiling)

        self._post_power(token, key, power_w, start_at, period_minutes)
        self._post_curtailment(token, key, float(curtailment_w), start_at, period_minutes)
        return start_at

    def _login(self):
        result = self._request(
            "POST",
            "/v1/auth",
            body={"username": self.username, "password": self.password},
        )
        token = result.get("jwt") if isinstance(result, dict) else None
        if not token:
            raise ApiError(200, "Antwort enthält kein JWT.")
        return token

    def _get_asset(self, token, plant_key):
        asset = self._request("GET", self._asset_path(plant_key), token=token)
        if not isinstance(asset, dict):
            raise ApiError(200, "Unerwartetes Antwortformat für die Anlage.")
        return asset

    def _post_power(self, token, plant_key, power_w, start_at, period_minutes):
        query = {
            "ds.category": "site-power",
            "ds.property": "generation",
            "ds.modality": "actuals",
            "ds.aspect": "deterministic",
            "ds.provider": self.account_id,
            "ds.name": "generation",
        }
        body = {
            "format": "compact-values",
            "quantity-name": "P",
            "unit": "W",
            "periodicity": [f"R/{period_token(period_minutes)}"],
            "start": format_instant(start_at),
            "interval-type": "[,[",
            "time-zone": "UTC",
            "instantaneous": False,
            "values": [power_w],
        }
        self._request(
            "POST",
            self._asset_path(plant_key) + "/data-streams/time-series",
            token=token,
            query=query,
            body=body,
        )

    def _post_curtailment(self, token, plant_key, curtailment_w, start_at, period_minutes):
        query = {
            "ds.category": "site-power",
            "ds.property": "available-generation-capacity.curtailment",
            "ds.modality": "plan",
            "ds.aspect": "principal-value",
        }
        body = {
            "value": curtailment_w,
            "unit": "W",
            "start-instant": format_instant(start_at),
            "end-instant": format_instant(start_at + timedelta(minutes=period_minutes)),
            "time-zone": "UTC",
        }
        self._request(
            "POST",
            self._asset_path(plant_key) + "/availabilities",
            token=token,
            query=query,
            body=body,
        )

    def _asset_path(self, plant_key):
        account = urllib.parse.quote(self.account_id, safe="")
        key = urllib.parse.quote(plant_key, safe="")
        return f"/v1/accounts/{account}/portfolios/{PORTFOLIO_ID}/assets/{key}"

    def _request(self, method, path, token=None, body=None, query=None):
        url = BASE_URL + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            message = raw
            try:
                payload = json.loads(raw)
                message = payload.get("error-message") or payload.get("name") or raw
            except json.JSONDecodeError:
                pass
            raise ApiError(exc.code, message) from exc
