#!/usr/bin/env python3
"""Login an der enercast-API und Ausgabe der Assets des Accounts."""

import getpass
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://rest-api.enercast.de"
PORTFOLIO_ID = "default"
ENV_PATH = Path(__file__).with_name(".env")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request(method, path, token=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
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


def login(username, password):
    result = request(
        "POST",
        "/v1/auth",
        body={"username": username, "password": password},
    )
    token = result.get("jwt") if isinstance(result, dict) else None
    if not token:
        raise ApiError(200, "Antwort enthält kein JWT.")
    return token


def list_assets(token, account_id):
    path = f"/v1/accounts/{account_id}/portfolios/{PORTFOLIO_ID}/assets"
    assets = request("GET", path, token=token)
    if not isinstance(assets, list):
        raise ApiError(200, "Unerwartetes Antwortformat für die Asset-Liste.")
    return assets


def plant_key(asset):
    keys = asset.get("keys")
    if isinstance(keys, dict):
        return keys.get("plant-key")
    return None


def print_assets(assets):
    if not assets:
        print("Keine Assets gefunden.")
        return

    print(json.dumps(assets, indent=2, ensure_ascii=False))
    print(f"\n{len(assets)} Asset(s)")
    for asset in assets:
        name = asset.get("name") or asset.get("asset-id") or "?"
        key = plant_key(asset) or "kein plant-key"
        print(f"{name}: {key}")


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


def main():
    env = load_env(ENV_PATH)
    username = env.get("ENERCAST_USERNAME") or input("Benutzername: ").strip()
    if not username:
        print("Benutzername fehlt.", file=sys.stderr)
        return 1
    password = env.get("ENERCAST_PASSWORD") or getpass.getpass("Passwort: ")
    account_id = env.get("ENERCAST_ACCOUNT_ID") or username

    try:
        token = login(username, password)
        assets = list_assets(token, account_id)
    except ApiError as exc:
        print(f"Fehler ({exc.status}): {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Verbindung fehlgeschlagen: {exc.reason}", file=sys.stderr)
        return 1

    print_assets(assets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
