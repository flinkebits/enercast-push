# enercast-api (Zähler / Langwieder)

Live-Wirkleistung und PV-Abregelung vom Huawei-SmartLogger (Modbus TCP) in die [enercast](https://rest-api.enercast.de)-Cloud. Messung in **5-Minuten-UTC-Intervallen**, zwischengespeichert in SQLite und bei Netzausfall später nachgeladen.

## Komponenten

| Datei | Rolle |
|--------|--------|
| `collect_modbus2enercast.py` | Dämon: Modbus alle 3 s, Mittelwert pro 5-Min-Fenster → Cache |
| `post_modbus2enercast.py` | Dämon: `pending` aus Cache → enercast API |
| `interval_store.py` | SQLite, Intervall-Raster, Status |
| `enercast_live.py` | API-Client (Login, Power, Abregelung) |
| `read_modbus_power.py` | Debug: Modbus-Werte jede Sekunde auf der Konsole |
| `example_send.py` | Manuell einen Wert senden |
| `list_assets.py` | Anlagen des Accounts auflisten |

## Schnellstart (Zähler, User `zaehler`)

Installationspfad: **`/home/zaehler/enercast`**

```bash
cd /home/zaehler/enercast
git pull
cp .env.example .env   # einmalig: Zugangsdaten eintragen
python3 -m unittest
```

Zum Testen (Vordergrund):

```bash
python3 collect_modbus2enercast.py
python3 post_modbus2enercast.py --once
```

- **Cache:** `data/modbus_intervals.db` (Ordner `data/` wird automatisch angelegt, liegt in `.gitignore`)
- **Konfiguration:** `.env` im gleichen Verzeichnis (`ENERCAST_USERNAME`, `ENERCAST_PASSWORD`, `ENERCAST_PLANT_KEY`)
- Optional: `ENERCAST_INTERVAL_DB` für einen anderen DB-Pfad (siehe `.env.example`)

Modbus-Ziel (SmartLogger) steht in `collect_modbus2enercast.py`: `HOST`, `PORT`.

## Hintergrunddienst (systemd)

Unit-Dateien liegen unter `deploy/`. Einmalig als root:

```bash
sudo cp deploy/enercast-modbus-collect.service deploy/enercast-modbus-post.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now enercast-modbus-collect enercast-modbus-post
```

Wartung:

```bash
sudo systemctl restart enercast-modbus-collect enercast-modbus-post
journalctl -u enercast-modbus-collect -u enercast-modbus-post -f
systemctl status enercast-modbus-collect enercast-modbus-post
```

Nach `git pull` die Dienste neu starten.

## Optional: DB außerhalb des Clones

Wenn die SQLite-Datei nicht unter `data/` im Repo liegen soll:

```bash
sudo install -d -o zaehler -g zaehler -m 0750 /var/lib/zaehler/enercast
```

In `.env`:

```env
ENERCAST_INTERVAL_DB=/var/lib/zaehler/enercast/modbus_intervals.db
```

Collect und post müssen denselben Pfad verwenden (gleiche `.env` bzw. gleiche Umgebung).

## Tests

```bash
python3 -m unittest
```

Ohne `.env` und ohne Modbus-Netzwerk (Unit-Tests für Cache, Bucket-Logik und Mock-Upload).
