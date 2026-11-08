"""Offline-Tests für gerätebericht.py: kein Netz, nur Aufbau und PDF."""

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gerätebericht as g
from gerätebericht import (
    BERLIN,
    INVERTER_TABLE_PT,
    TABLE_PT,
    collect_report,
    dev_type,
    footer_parts,
    format_plant_list,
    inverter_parameter_grid,
    string_detail_rows,
    format_value,
    devices_by_name,
    group_devices,
    label_for,
    load_env,
    make_pdf,
    parameter_grid,
    parameter_rows,
    plant_rows,
    render_report,
    select_plants,
    unit_for,
)


class EnvTest(unittest.TestCase):
    def test_load_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# Kommentar\nA=1\nB="mit leerzeichen"\nleer=\nC=x=y\n')
            env = load_env(path)
        self.assertEqual(env, {"A": "1", "B": "mit leerzeichen", "leer": "", "C": "x=y"})

    def test_load_env_missing(self):
        self.assertEqual(load_env(Path("/nonexistent/.env")), {})

    def test_credentials_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "FUSIONSOLAR_HOST=h\nFUSIONSOLAR_USER=u\n"
                "FUSIONSOLAR_SYSTEM_CODE=c\nFUSIONSOLAR_GAP_SECONDS=4.5\n"
            )
            old = g.ENV_PATH
            g.ENV_PATH = path
            try:
                host, user, code, plant, gap = g.credentials()
            finally:
                g.ENV_PATH = old
        self.assertEqual((host, user, code, plant, gap), ("h", "u", "c", "", 4.5))

    def test_credentials_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("FUSIONSOLAR_HOST=h\n")
            old = g.ENV_PATH
            g.ENV_PATH = path
            try:
                with self.assertRaises(SystemExit):
                    g.credentials()
            finally:
                g.ENV_PATH = old


class FormatTest(unittest.TestCase):
    def test_format_value(self):
        self.assertEqual(format_value(None), "—")
        self.assertEqual(format_value(True), "ja")
        self.assertEqual(format_value(5.0), "5")
        self.assertEqual(format_value(3.21000), "3.21")
        self.assertEqual(format_value("text"), "text")

    def test_units(self):
        self.assertEqual(unit_for("active_power"), "kW")
        self.assertEqual(unit_for("total_yield"), "kWh")
        self.assertEqual(unit_for("temperature"), "°C")
        self.assertEqual(unit_for("a_u"), "V")
        self.assertEqual(unit_for("b_i"), "A")
        self.assertEqual(unit_for("ab_u"), "V")
        self.assertEqual(unit_for("pv12_u"), "V")
        self.assertEqual(unit_for("pv12_i"), "A")
        self.assertEqual(unit_for("mppt_3_cap"), "kWh")
        self.assertEqual(unit_for("insulation_resistance"), "MΩ")
        self.assertEqual(unit_for("unbekannt"), "—")

    def test_labels(self):
        self.assertEqual(label_for("active_power"), "Wirkleistung")
        self.assertEqual(label_for("pv2_i"), "PV-String 2 Strom")
        self.assertEqual(label_for("c_u"), "Außenleiter C Spannung")
        self.assertEqual(label_for("insulation_resistance"), "Isolationswiderstand")
        self.assertEqual(label_for("irgendwas"), "irgendwas")

    def test_parameter_rows_state_label(self):
        rows = parameter_rows({"inverter_state": 513, "active_power": 1.5, "pv9_u": None})
        self.assertEqual(rows[0], ("Wirkleistung", "1.5", "kW"))
        self.assertIn("leistungsbegrenzt", rows[1][1])
        self.assertEqual(rows[1][0], "Betriebszustand")
        self.assertEqual(len(rows), 2)

    def test_table_point_size(self):
        self.assertEqual(TABLE_PT, 10.5)
        self.assertEqual(INVERTER_TABLE_PT, 8.5)
        self.assertLess(INVERTER_TABLE_PT, TABLE_PT)


class GroupTest(unittest.TestCase):
    def test_dev_type(self):
        self.assertEqual(dev_type({"devTypeId": 1}), 1)
        self.assertEqual(dev_type({"devTypeId": "63"}), 63)
        self.assertEqual(dev_type({}), -1)

    def test_group_order_logger_first(self):
        devs = [
            {"id": 1, "devTypeId": 39},
            {"id": 2, "devTypeId": 1},
            {"id": 3, "devTypeId": 63},
            {"id": 4, "devTypeId": 38},
        ]
        groups = group_devices(devs)
        self.assertEqual([t for t, _ in groups], [63, 1, 38, 39])

    def test_devices_by_name(self):
        devs = [
            {"devName": "WR_10", "devTypeId": 1},
            {"devName": "logger hof", "devTypeId": 63},
            {"devName": "WR_02", "devTypeId": 1},
        ]
        self.assertEqual(
            [device["devName"] for device in devices_by_name(devs)],
            ["logger hof", "WR_02", "WR_10"],
        )
        groups = group_devices(devs)
        inverters = next(items for type_id, items in groups if type_id == 1)
        self.assertEqual([device["devName"] for device in inverters], ["WR_02", "WR_10"])

    def test_select_plants(self):
        found = [{"plantName": "Alpha", "plantCode": "NE=1"}, {"plantName": "Beta", "plantCode": "NE=2"}]
        self.assertEqual(select_plants(found, ""), found)
        self.assertEqual(select_plants(found, "NE=2"), [found[1]])
        with self.assertRaises(SystemExit):
            select_plants(found, "Gamma")

    def test_plant_rows_date(self):
        rows = plant_rows({"plantName": "Alpha", "plantCode": "NE=1", "gridConnectionDate": 1700000000000})
        labels = dict(rows)
        self.assertEqual(labels["Netzanschlussdatum"], "2023-11-14")
        self.assertNotIn("Adresse", labels)
        rows = plant_rows({"gridConnectionDate": "2026-05-04T14:24:28+01:00"})
        self.assertEqual(rows[0], ("Netzanschlussdatum", "2026-05-04"))


class GridTest(unittest.TestCase):
    def test_pairs_values_with_units(self):
        rows = parameter_rows({"active_power": 1.5, "inverter_state": 512, "temperature": 20})
        grid = parameter_grid(rows)
        self.assertEqual(len(grid), 2)
        self.assertEqual(grid[0][0], "Wirkleistung")
        self.assertEqual(grid[0][1], "1.5 kW")
        self.assertEqual(grid[0][2], "Betriebszustand")
        self.assertIn("netzparallel", grid[0][3])
        self.assertEqual(grid[1][0], "Innentemperatur")
        self.assertEqual(grid[1][1], "20 °C")
        self.assertEqual(grid[1][2], "")
        self.assertEqual(grid[1][3], "")

    def test_string_details_one_row_per_string(self):
        data = {
            "pv10_i": 1,
            "active_power": 4,
            "pv2_u": 700,
            "pv2_i": 2.5,
            "pv10_u": 650,
            "insulation_resistance": 2.5,
            "pv3_u": None,
            "pv1_u": 1100,
        }
        self.assertEqual(string_detail_rows(data), [
            ["PV1", "1100", "—"],
            ["PV2", "700", "2.5"],
            ["PV10", "650", "1"],
        ])
        grid = inverter_parameter_grid(data)
        self.assertEqual(grid[0][0], "Wirkleistung")
        self.assertEqual(grid[0][2], "Isolationswiderstand")
        self.assertNotIn("PV1", [cell for row in grid for cell in row])

    def test_format_plant_list(self):
        text = format_plant_list([
            {"plantName": "Alpha", "plantCode": "NE=1", "capacity": 99, "gridConnectionDate": "2024-03-12T00:00:00+01:00"},
            {"plantName": "Beta", "plantCode": "NE=2"},
        ])
        self.assertTrue(text.startswith("2 Anlagen"))
        self.assertIn("Alpha  NE=1  (99 kW, 2024-03-12)", text)
        self.assertIn("Beta  NE=2", text)


class ArgsTest(unittest.TestCase):
    def test_help_exits(self):
        for flag in ("-h", "--help"):
            with self.assertRaises(SystemExit) as caught:
                with redirect_stdout(StringIO()) as out:
                    g.parse_args([flag])
            self.assertEqual(caught.exception.code, 0)
            text = out.getvalue()
            self.assertIn("--list-plants", text)
            self.assertIn("-L", text)
            self.assertIn("--help", text)

    def test_list_plants_flag(self):
        self.assertTrue(g.parse_args(["-L"]).list_plants)
        self.assertTrue(g.parse_args(["--list-plants"]).list_plants)
        self.assertFalse(g.parse_args([]).list_plants)


class FooterTest(unittest.TestCase):
    def test_footer_parts(self):
        created = datetime(2026, 11, 8, 11, 28, tzinfo=BERLIN)
        left, center, note, right = footer_parts("Demo-Anlage", created, 2, 5)
        self.assertEqual(left, "Demo-Anlage")
        self.assertEqual(center, "Erstellt am 08.11.2026, 11:28 Uhr")
        self.assertEqual(note, "Messwerte zum Erstellzeitpunkt")
        self.assertEqual(right, "Seite 2 von 5")


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, path, payload, retries=3):
        self.calls.append((path, payload))
        if path == "/thirdData/stations":
            return {"data": {"list": [{"plantName": "Demo", "plantCode": "NE=1"}], "pageCount": 1}}
        if path == "/thirdData/getDevList":
            return {"data": [
                {"id": 11, "devName": "Logger1", "devDn": "NE=L1", "esnCode": "SN-L", "devTypeId": 63},
                {"id": 22, "devName": "WR1", "devDn": "NE=I1", "esnCode": "SN-I", "devTypeId": 1, "invType": "SUN2000-10KTL"},
                {"id": 33, "devName": "Zähler", "esnCode": "SN-Z", "devTypeId": 39},
            ]}
        if path == "/thirdData/getDevRealKpi":
            if payload["devTypeId"] == "63":
                raise g.FusionSolarError(20013, "Gerätetyp nicht unterstützt")
            if payload["devTypeId"] == "1":
                return {"data": [{"devId": 22, "dataItemMap": {"active_power": 3.21, "inverter_state": 512}}]}
        if path == "/rest/openapi/pvms/nbi/v1/device/history":
            assert payload["devDn"] == "NE=L1", payload
            return {"data": [
                {"collectTime": 1000, "dataItems": {"total_yield": 1.0}},
                {"collectTime": 2000, "dataItems": {"total_yield": 1234.5}},
            ]}
        raise AssertionError(f"unerwarteter Aufruf {path} {payload}")


class CollectTest(unittest.TestCase):
    def test_collect_report_maps_kpi(self):
        client = FakeClient()
        model = collect_report(client, "")
        self.assertEqual(len(model), 1)
        entry = model[0]
        self.assertEqual(len(entry["devices"]), 3)
        self.assertEqual(entry["kpi"]["11"], {"total_yield": 1234.5})
        self.assertEqual(entry["kpi_source"]["11"], "history")
        self.assertEqual(entry["kpi"]["22"]["active_power"], 3.21)
        self.assertEqual(entry["kpi_source"]["22"], "real")
        kpi_calls = [p for path, p in client.calls if path == "/thirdData/getDevRealKpi"]
        self.assertEqual(sorted(p["devTypeId"] for p in kpi_calls), ["1", "63"])


def _model(param_count=5):
    kpi = {f"key_{i}": i for i in range(param_count)}
    return [{
        "plant": {"plantName": "Demo-Anlage", "plantCode": "NE=1", "gridConnectionDate": 1700000000000},
        "devices": [
            {"id": 11, "devName": "Logger Übung", "devDn": "NE=L1", "esnCode": "SN-L", "devTypeId": 63},
            {"id": 22, "devName": "WR1", "devDn": "NE=I1", "esnCode": "SN-I", "devTypeId": 1, "invType": "SUN2000"},
        ],
        "kpi": {"11": {"total_yield": 100.0}, "22": kpi},
        "kpi_source": {"11": "history", "22": "real"},
    }]


class PdfTest(unittest.TestCase):
    def test_render_embeds_font_and_table_size(self):
        now = datetime(2026, 11, 8, 11, 28, tzinfo=BERLIN)
        pdf = make_pdf(_model(), now, "eu5.example")
        self.assertGreaterEqual(pdf.page, 3)
        pdf.set_compression(False)
        raw = bytes(pdf.output())
        self.assertTrue(raw.startswith(b"%PDF"))
        self.assertIn(b"/FontFile2", raw)
        self.assertIn(b"DejaVuSans", raw)
        self.assertIn(b"10.50 Tf", raw)
        self.assertIn(b"8.50 Tf", raw)
        self.assertIn(b"18.00 Tf", raw)

    def test_two_plants_start_on_new_pages(self):
        now = datetime(2026, 11, 8, 11, 28, tzinfo=BERLIN)
        model = _model() + [{
            "plant": {"plantName": "Zweite", "plantCode": "NE=2"},
            "devices": [{"id": 5, "devName": "WR2", "devTypeId": 38, "esnCode": "SN-2"}],
            "kpi": {"5": {"active_power": 0.5}},
            "kpi_source": {"5": "real"},
        }]
        pdf = make_pdf(model, now, "eu5.example")
        self.assertGreaterEqual(pdf.page, 5)
        self.assertEqual(pdf.plant_by_page[1], "Demo-Anlage")
        self.assertEqual(pdf.plant_by_page[pdf.page], "Zweite")
        self.assertEqual(pdf.plant_by_page[2], "Demo-Anlage")

    def test_long_parameter_table_breaks_page(self):
        now = datetime(2026, 11, 8, 11, 28, tzinfo=BERLIN)
        pdf = make_pdf(_model(param_count=120), now, "eu5.example")
        self.assertGreater(pdf.page, 1)
        raw = render_report(_model(param_count=2), now, "eu5.example")
        self.assertTrue(raw.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
