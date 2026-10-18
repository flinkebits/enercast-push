# FusionSolar Northbound API — historische Wechselrichterdaten

Stand: 18. Oktober 2026. Quellen sind die öffentlichen Huawei-Referenzen
*SmartPVMS Northbound Interface Reference* (V6, u. a. V600R024C20 und
SmartPVMS 25.4.0 / 26.1.0). Die Cloud-Version des eigenen Portals kann
Felder und Limits abweichen lassen; maßgeblich ist die Referenz zur
installierten SmartPVMS-Version.

Ziel dieser Notiz: historische **Leistungsdaten einzelner Wechselrichter**
abfragen. Das ist der Cloud-Weg neben dem lokalen Modbus-SmartLogger
in diesem Repo (dort nur die letzten 16 Fünf-Minuten-Fenster).

## Zugang

Die API heißt Northbound Interface (NBI) und spricht nur HTTPS, Body und
Antwort als JSON, Methode fast durchgängig `POST`.

Es gibt drei Konten, und nur das dritte loggt sich an der API ein:

| Konto | Woher | Für `/thirdData/login` |
| --- | --- | --- |
| Anlagenbesitzer (Owner) | App oder Web, sieht die eigene Anlage | nicht verwendbar, darf auch kein API-Konto anlegen |
| Firmenadministrator | Installer-Registrierung auf der Login-Seite. In **System > Company Management > User Management** steht in der Spalte Rolle der Firmenadministrator | Web-Login, nicht das API-Passwort. Legt bis zu fünf API-Konten an |
| Northbound-API-Konto | vom Firmenadministrator unter Northbound Management angelegt | das sind `userName` und `systemCode` |

Das eigene FusionSolar-Login (E-Mail, Telefon, Installer-Name) ist damit
kein API-Login. Huawei verbietet ausdrücklich, dass der API-Benutzername
dem Login-Namen der Weboberfläche gleicht. Wer nur Besitzer der Anlage
ist, muss den Installer bitten, ein API-Konto anzulegen und diese Anlage
daran zu binden. Wer selbst Firmenadministrator ist, legt ein **zweites**
Paar aus Benutzername und Passwort an. Das Feld „Associated account“ ist
nur das SmartPVMS-Konto für Hinweismails, nicht die Anmeldung.

Anlegen: **System > Company Management > Northbound Management > Add**
(ältere Oberfläche: **System > System > Company Management**). Eintragen:
Benutzername, Passwort, Ablaufdatum (`Deadline`; danach liefert das Login
`20003`), zugehöriges Konto, gebundene Firma oder Anlage. In der neueren
Referenz (25.2.0) schaltet **Basic APIs** die Basisdaten frei. In der
älteren Oberfläche die Schnittstellen einzeln aktivieren, mindestens
Anlagenliste, Geräteliste und historische Gerätedaten, und unter
String- bzw. Haushalts-Wechselrichter die Geräte freigeben. Ohne diese
Haken: `401` oder `20009`.

Host ist die Portal-Domain nach dem Web-Login, nicht eine feste
Huawei-Adresse. In Europa häufig `eu5.fusionsolar.huawei.com`, sonst
z. B. `intl.fusionsolar.huawei.com` oder ein Cluster-Host, auf den das
Portal umleitet. Dieselbe Domain für Login und Datenabfragen verwenden.

```text
https://{portal}/thirdData/...
https://{portal}/rest/openapi/pvms/nbi/v1/...
```

## Authentifizierung

Es gibt kein Token zum Abholen im Portal oder bei Huawei. Die dauerhaften
Zugangsdaten sind der **Benutzername und das Passwort des API-Kontos**.
Das Passwort heißt im JSON `systemCode`, nicht `password`.

`POST /thirdData/login`

```json
{ "userName": "<api-benutzername>", "systemCode": "<api-passwort>" }
```

Bei `success: true` und `failCode: 0` ist `data` leer. Das Token steht nur
im **Response-Header** `xsrf-token` (Huawei schreibt ihn `XSRF-TOKEN`).
Jede folgende Anfrage schickt denselben Wert als Request-Header
`XSRF-TOKEN`, plus `Content-Type: application/json`.

- Gültigkeit 30 Minuten. Nutzung innerhalb des Fensters verlängert es
  (Angabe in der Token-Beschreibung SmartPVMS 25.4.0). Danach neu einloggen.
- Ein Konto hat **eine** Sitzung. Ein erneutes Login macht das vorherige
  Token ungültig (`305` = nicht eingeloggt).
- Login: höchstens 5 Aufrufe je 10 Minuten. Fünf falsche Passwörter in
  10 Minuten sperren das Konto 30 Minuten (`407` bzw. `20400`).
- Logout, nur wenn nötig: `POST /thirdData/logout` mit Body
  `{ "xsrfToken": "..." }`.

Es gibt zusätzlich `POST /rest/openapi/pvms/nbi/v1/token` für neuere
Northbound-Credentials. Für ein klassisches API-Konto aus dem
Northbound-Management ist `/thirdData/login` der dokumentierte Weg.
Ab SmartPVMS 26.1.0 beschreibt Huawei außerdem OAuth; das ändert die
bestehenden `thirdData`-Pfade nicht.

## Ablauf

1. Anlagen: `POST /thirdData/stations` mit `{ "pageNo": 1 }`.
   Antwortfeld `data.list[].plantCode` (Beispiel `NE=12345678`).
   Seite bis 100 Anlagen. Optional `gridConnectedStartTime` /
   `gridConnectedEndTime` in Millisekunden.
2. Geräte: `POST /thirdData/getDevList` mit
   `{ "stationCodes": "<plantCode>[,...]" }` (max. 100 Anlagen).
   Pro Gerät: `id` (Long), `devDn` (String, neuere Referenz, Form
   `NE=…`), `esnCode` (SN), `devName`, `devTypeId`, `invType`.
3. Historie nur für den **Wechselrichter**, nicht für den SmartLogger.
   `devTypeId` 2 (SmartLogger) ist in den Historie-Schnittstellen nicht
   enthalten. Den Typ aus der Geräteliste nehmen, nicht raten:
   - `1` String-Wechselrichter (typisch SUN2000 …KTL, Gewerbe)
   - `38` Haushalts-Wechselrichter
4. Historie abfragen (nächster Abschnitt). `devIds` ist die kommagetrennte
   Liste der `id`-Werte, `sns` die der `esnCode`. Genau eines von beiden.
   `devDn` gehört nur zur neuen Schnittstelle.

`id` kann ein großer vorzeichenbehafteter Long sein (Beispiel
`-214543629611879`). In der kommagetrennten Liste als Text schicken.

## 5-Minuten-Leistung: zwei Schnittstellen

Beide liefern Abtastwerte im 5-Minuten-Raster (`collectTime` als
Unix-Zeit in Millisekunden, Abstand 300000). `active_power` ist bei
Wechselrichtern die **Wirkleistung in kW**, nicht die Energie des
Intervalls.

### Empfohlen ab SmartPVMS 25.3: eine Anlage, 24 Stunden

Ab SmartPVMS 25.3 / in der Referenz 26.1.0 (Stand 2026-07-27) rät Huawei
von `getDevHistoryKpi` ab und verweist auf:

`POST /rest/openapi/pvms/nbi/v1/device/history`

```json
{
  "devDn": "NE=37884931",
  "devTypeId": 1,
  "startTime": 1758038400000,
  "endTime": 1758124800000
}
```

- Ein Gerät, höchstens 24 Stunden 5-Minuten-Daten pro Aufruf.
- `devDn` aus der Geräteliste, nicht die numerische `id`.
- Zusätzliche Typen gegenüber der alten Schnittstelle, u. a. `63`
  (verteilter SmartLogger). String- und Haushalts-Wechselrichter bleiben
  `1` und `38`.
- Antwort: `data[].collectTime` und `data[].dataItems` (nicht
  `dataItemMap`).
- Die abgerufene 25.4.0-Seite nennt für diesen Pfad **keine** eigene
  Tagesquote. `407` existiert weiterhin als allgemeines Drossel-Signal.
  Die Quote am eigenen Konto prüfen, bevor ein Backfill läuft.

### Weiter verbreitet: bis 10 Geräte, 3 Tage

`POST /thirdData/getDevHistoryKpi`

```json
{
  "devIds": "214060404588862,213472461631079",
  "devTypeId": 1,
  "startTime": 1501862400000,
  "endTime": 1501866000000
}
```

- Höchstens **10 Geräte desselben Typs** und ein Fenster von **drei
  Tagen** (`30031`, `30032`). `startTime` muss vor `endTime` liegen
  (`20023`, `20604`).
- Antwort: `data[].devId`, `data[].sn`, `data[].collectTime`,
  `data[].dataItemMap`.
- Tagesquota pro API-Konto:
  `Summe über Gerätetypen von aufrunden(Anzahl / 10) + 24`.
  Ein Wechselrichter und keine weiteren Typen: 25 Aufrufe am Tag.
  20 Wechselrichter und 20 Zähler: 28.
- Zusätzlich **ein** gleichzeitiger Aufruf pro Minute. Schnelleres
  Pollen liefert `407`.
- Nutzer, die das Konto am oder vor dem 30. Juni 2022 angelegt haben,
  bleiben auf der alten, nicht erhöhbaren Quota.

Für einen Jahres-Backfill eines Wechselrichters sind das etwa
365 / 3 ≈ 122 Aufrufe, also mehrere Tage bei 25 Aufrufen/Tag und
mindestens 60 Sekunden Abstand. Mehrere Wechselrichter desselben Typs
in einem Aufruf bündeln (bis 10).

`POST /thirdData/getDevFiveMinutes` ist die Vorgängerschnittstelle
(ein Tag je `collectTime`, Gerätezeitzone). Ab NetEco
V600R022C00CP1202 nicht mehr für neue Nutzer. Nicht verwenden.

### Was in `dataItemMap` / `dataItems` für Leistung zählt

Wechselrichter `devTypeId` 1, Auszug. Einheiten aus der V6-Referenz;
25.2.0 erweitert die PV-Strings eines String-Wechselrichters auf 36
(ältere Listen enden bei `pv28_*`).

| Key | Bedeutung | Einheit |
| --- | --- | --- |
| `active_power` | Wirkleistung | kW |
| `reactive_power` | Blindleistung | kVar |
| `mppt_power` | DC-Eingangsleistung, Summe MPPT | kW |
| `day_cap` | Ertrag seit Tagesbeginn (Zähler, nicht Intervall) | kWh |
| `total_cap` | Gesamtertrag | kWh |
| `elec_freq` | Netzfrequenz | Hz |
| `power_factor` | Leistungsfaktor | — |
| `temperature` | Innentemperatur | °C |
| `efficiency` | Wirkungsgrad laut Hersteller | % |
| `a_u` `b_u` `c_u` | Außenleiterspannung | V |
| `ab_u` `bc_u` `ca_u` | verkettete Spannung | V |
| `a_i` `b_i` `c_i` | Netzstrom | A |
| `pvN_u` / `pvN_i` | Stringspannung / -strom | V / A |
| `mppt_N_cap` | DC-Ertrag je MPPT | kWh |
| `inverter_state` | Betriebszustand, siehe unten | — |
| `open_time` / `close_time` | Start / Stopp | ms |

`inverter_state` (Auszug): `512` netzparallel, `513` netzparallel und
**leistungsbegrenzt**, `514` Eigen-Derating, `772` Abschaltung wegen
Leistungsbegrenzung, `45056` Kommunikationsunterbrechung (vom
SmartLogger geschrieben). `513` ist ein Hinweis auf Abregelung, aber
kein Sollwert in Watt wie das Modbus-Register in diesem Repo.

Energie eines 5-Minuten-Schritts ist die Differenz von `day_cap`.
Am Tageswechsel (Zeitzone der Anlage) setzt der Zähler zurück; dort
nicht über Mitternacht differenzieren. `active_power * 5/60` ist nur
eine Näherung der Intervallenergie.

Mehrere Tage in einem Aufruf gehen nur bis zur Grenze der Schnittstelle.
`getDevHistoryKpi` nimmt ein geschlossenes Intervall `[startTime, endTime)`
von höchstens **drei Tagen** (`30032`, wenn das Fenster länger ist). Drei
Kalendertage sind ein Aufruf, der vierte Tag der nächste, mit mindestens
60 Sekunden Abstand. Die neuere Schnittstelle
`/rest/openapi/pvms/nbi/v1/device/history` nimmt höchstens **24 Stunden**
und ein Gerät. Tagesertrag ohne Leistungskurve ist großzügiger:
`getDevKpiDay` liefert mit einem `collectTime` alle vorhandenen Tage
dieses Monats.

Wie weit die 5-Minuten-Werte in die Vergangenheit reichen, setzt die
Referenz von `getDevHistoryKpi` nicht fest. Begrenzt ist die Länge eines
Aufrufs (3 Tage bzw. 24 Stunden), nicht das älteste erlaubte Datum. Die
Fehler `20026` („nur die letzten 365 Tage“) und `20027` („nicht mehr als
31 Tage“) stehen in der allgemeinen Fehlerliste und sind dort nicht dieser
Schnittstelle zugeordnet. Die sechs Monate im Besitzer-Handbuch gelten
für Laufdaten eines **entkoppelten** Geräts, nicht als Abfragefrist einer
verbundenen Anlage. Praktisch das Fenster in Dreitages-Schritten nach
hinten schieben, bis `data` leer ist oder ein Fehlercode kommt.

## Gröbere Historie, wenn das 5-Minuten-Raster zu teuer ist

Diese Schnittstellen haben keine Wirkleistungs-Zeitreihe. `collectTime`
wählt den Zeitraum; die Cloud rechnet in der **Zeitzone des Geräts bzw.
der Anlage** und liefert alle vorhandenen Punkte des Monats, Jahres oder
Tags.

| Pfad | Fenster | Wechselrichter-Felder |
| --- | --- | --- |
| `/thirdData/getDevKpiDay` | alle Tage des Monats von `collectTime` | `product_power` kWh, `installed_capacity` kW, `perpower_ratio` h |
| `/thirdData/getDevKpiMonth` | alle Monate des Jahres | dieselben Keys |
| `/thirdData/getDevKpiYear` | Jahre | dieselben Keys |
| `/thirdData/getKpiStationHour` | Stunden des Tages, **Anlage** | u. a. `inverterYield`, `PVYield`, `inverter_power` (Huawei rät von `inverter_power` ab) |

Geräte-KPI: max. 100 Geräte eines Typs, Typen `1`, `38`, `39`, `41`.
Quota analog `aufrunden(Anzahl/100) + 24` Aufrufe am Tag, ein Aufruf
pro Minute. Für einen einzelnen Wechselrichter genügt ein
`getDevKpiDay` für den ganzen Monat und ein `getDevKpiYear` für die
Jahreswerte.

Echtzeit, kein Ersatz für Historie: `POST /thirdData/getDevRealKpi`
(max. 100 Geräte, Quota über 5 Minuten statt pro Tag).

Schnittstellen unter „Restricted Access“ (u. a. ältere
Anlagen-Leistungshistorie) brauchen eine Freigabe durch den
Huawei-Support. Für diesen Zweck nicht einplanen.

## Fehlercodes, die beim Historienabruf vorkommen

| Code | Bedeutung |
| --- | --- |
| `0` | in Ordnung |
| `305` | nicht eingeloggt, Token fehlt oder ist durch neues Login ungültig |
| `401` | keine Freigabe für diese Schnittstelle |
| `407` | Quota oder Mindestabstand überschritten; Frequenz senken, bei Systemlimit `403`/`429` mindestens eine Minute warten |
| `20001` | API-Konto existiert nicht (in Login-Beispielen auch allgemein für Fehlschlag) |
| `20003` | API-Konto abgelaufen |
| `20006` | Gerät passt nicht zum `devTypeId` |
| `20008` | Gerät nicht im Konto |
| `20009` | KPI nicht freigeschaltet oder nicht vorhanden |
| `20011` | Geräteliste leer |
| `20012` | Zeit fehlt |
| `20013` | Gerätetyp von dieser Schnittstelle nicht unterstützt |
| `20023` / `20604` | `startTime` nicht vor `endTime` |
| `20400` | Login: falsches Passwort, Sperre, abgelaufenes Passwort oder zu viele Sitzungen |
| `30031` | mehr als 10 Geräte |
| `30032` | Fenster länger als drei Tage |

Antworten mit HTTP 200 können trotzdem `success: false` tragen.
`failCode` prüfen, nicht nur den HTTP-Status.

## Was das für einen Client heißt

- Token cachen und erst bei `305` neu anmelden.
- Zwischen Historienaufrufen mindestens 60 Sekunden liegen lassen und
  `407` mit Backoff behandeln. Ein zweiter Prozess mit demselben
  API-Konto zerstört das Token.
- Für ein 5-Minuten-Profil über viele Tage und mehrere Wechselrichter
  ist `getDevHistoryKpi` der ergiebigere Aufruf (10 Geräte × 3 Tage).
  Für laufendes Nachziehen eines Geräts ist
  `/rest/openapi/pvms/nbi/v1/device/history` der von Huawei genannte
  Nachfolger (1 Gerät × 24 Stunden), sofern das Portal ihn schon führt.
- `active_power` ist der Leistungswert in kW zum Zeitstempel
  `collectTime`. Er ersetzt nicht den Mittelwert aus 3-Sekunden-Modbus
  in `collect_modbus2enercast.py`.

## Quellen

- [Login](https://support.huawei.com/enterprise/en/doc/EDOC1100349425/9e1a18d2/login-interface)
  und [Token holen (25.4.0)](https://support.huawei.com/enterprise/en/doc/EDOC1100544235/61612573/obtaining-a-token)
- [Northbound-Konto anlegen (Installer, 26.1.0)](https://support.huawei.com/enterprise/en/doc/EDOC1100558399/76091675/managing-a-northbound-api-account)
- [Geräteliste](https://support.huawei.com/enterprise/en/doc/EDOC1100411805/c2b572a8/device-list-interface)
- [Gerätedaten inkl. `getDevHistoryKpi`](https://support.huawei.com/enterprise/en/doc/EDOC1100411805/2630ae1b/device-data-interfaces)
- [KPI-Liste 5-Minuten-Historie](https://support.huawei.com/enterprise/en/doc/EDOC1100411805/4223214/historical-device-data-interface)
- [Monitoring 25.4.0, neue Historie `/rest/openapi/pvms/nbi/v1/device/history`](https://support.huawei.com/enterprise/en/doc/EDOC1100544235/b9df65c2/monitoring)
- [Änderungsübersicht 26.1.0](https://support.huawei.com/enterprise/en/doc/EDOC1100569196)
  (dort: `getDevHistoryKpi` nicht mehr empfohlen)
