from datetime import datetime
from pathlib import Path
import hashlib
import tempfile
import unittest

from konfigurationsbericht import (
    BERLIN,
    ReportPDF,
    device_list_text,
    device_list_path,
    device_parameter_rows,
    footer_parts,
    format_epoch,
    merge_device_pages,
    select_plants,
    source_version,
    string_parameter_rows,
    summarize_behavior,
    triple_rows,
)


class DeviceParameterTest(unittest.TestCase):
    def test_keeps_filled_identity_and_translates_enums(self):
        rows = device_parameter_rows([
            {"id": 50012, "value": "6T2300000001"},
            {"id": 50009, "value": "SUN2000-215KTL-H3"},
            {"id": 50011, "value": "1.00"},
            {"id": 50013, "value": "3"},
            {"id": 50019, "value": "0"},
            {"id": 21029, "value": "1", "enumList": {"1": "Three-phase three-wire system"}},
            {"id": 24001, "value": "0", "enumList": {"0": "Disable"}},
            {"id": 50022, "value": ""},
            {"id": 10024, "name": "Insulation resistance", "value": "2.5"},
        ], {"warrantExpDeadlinedateStr": "2030-06-29"})
        self.assertEqual(rows, [
            ("Seriennummer", "6T2300000001"),
            ("Modell", "SUN2000-215KTL-H3"),
            ("Protokollversion", "1.00"),
            ("Modbus-Adresse", "3"),
            ("Geräteport", "0"),
            ("Garantieende", "2030-06-29"),
            ("Ausgangsmodus", "Dreiphasig, drei Leiter"),
            ("Stringanschluss-Erkennung", "aus"),
        ])

    def test_skips_names_the_font_cannot_draw(self):
        rows = device_parameter_rows([
            {"id": 50009, "value": "当日吸收电量"},
            {"id": 50012, "value": "SN1"},
        ])
        self.assertEqual(rows, [("Seriennummer", "SN1")])

    def test_hides_unset_warranty_date(self):
        rows = device_parameter_rows([], {"warrantExpDeadlinedateStr": "1970-01-01"})
        self.assertEqual(rows, [])


class StringParameterTest(unittest.TestCase):
    def test_portal_fields_in_three_columns(self):
        rows = string_parameter_rows({
            "pvIndex": 1,
            "manufacturer": "Ja Solar",
            "moduleVersion": "JAM66D45-615/LB-SF",
            "moduleType": "5",
            "standardPower": 615.0,
            "modulesNumPerString": 27,
            "fixedPower": 16605.0,
            "maxPowerPointVoltage": 39.96,
            "maxPowerPointCurrent": 15.39,
            "componentsNominalVoltage": 48.3,
            "nominalCurrentComponent": 16.1,
            "maxPowerTempCoef": -0.29,
            "voltageTempCoef": -0.25,
            "currentTempCoef": 0.045,
            "cellsNumPerModule": 132,
            "moduleProductionDate": 1759276800000,
            "firstDegradationDrate": 1.0,
            "secondDegradationDrate": 0.4,
            "fillFactor": None,
            "moduleRatio": None,
        })
        self.assertEqual(rows[0], ("Modulhersteller", "Ja Solar"))
        self.assertEqual(rows[2][1], "Typ N zweiseitig")
        self.assertEqual(rows[3], ("Pmax", "615 W"))
        self.assertEqual(rows[5][1], "16605 Wp")
        self.assertEqual(rows[6], ("Vmp", "39.96 V"))
        self.assertEqual(rows[8], ("Voc", "48.3 V"))
        self.assertEqual(rows[17][1], "—")
        grid = triple_rows(rows[:4])
        self.assertEqual([pair[0] for pair in grid[0]], ["Modulhersteller", "Modultyp", "Zelltyp"])
        self.assertEqual(grid[1][0][0], "Pmax")
        self.assertEqual(grid[1][1], ("", ""))
        self.assertEqual(grid[1][2], ("", ""))

    def test_install_date_from_millis(self):
        text = format_epoch(1759276800000)
        self.assertRegex(text, r"2025-10-0[12]")


class BehaviorSummaryTest(unittest.TestCase):
    def test_prints_shared_values_once_and_keeps_outliers(self):
        shared = {"id": 21096, "value": "0.050", "unit": "MΩ"}
        mode = {"id": 21115, "value": "0", "enumList": {"0": "No limit"}}
        rows = summarize_behavior([
            ("WR_01", [shared, mode, {"id": 21122, "value": "57.4", "unit": "%"}, {"id": 21034, "value": "1000.0", "unit": "V"}]),
            ("WR_02", [shared, mode, {"id": 21122, "value": "61.6", "unit": "%"}, {"id": 21034, "value": "1000.0", "unit": "V"}]),
            ("WR_13", [shared, mode, {"id": 21034, "value": "896.0", "unit": "V"}]),
        ])
        sections = dict(rows["sections"])
        self.assertEqual(sections["Isolierung"], [("Isolationswiderstand Schwelle", "0.050 MΩ")])
        self.assertEqual(sections["Leistungsregelung"], [("Wirkleistungsmodus", "keine Begrenzung")])
        self.assertEqual(rows["deviations"][0][0], "Stufe 1 Überspannung Schwelle")
        self.assertEqual(rows["deviations"][0][1][0][0], "1000.0 V")
        self.assertEqual(rows["per_device"]["WR_13"], [("Stufe 1 Überspannung Schwelle", "896.0 V", "1000.0 V")])
        self.assertNotIn("WR_01", rows["per_device"])

    def test_missing_value_is_a_deviation(self):
        rows = summarize_behavior([
            ("WR_01", [{"id": 230222695, "value": "1", "enumList": {"1": "Enable"}}]),
            ("WR_02", []),
        ])
        self.assertEqual(rows["sections"], [])
        self.assertEqual(rows["per_device"]["WR_02"], [("LFSM-O", "—", "ein")])

    def test_long_device_list_stays_on_one_line(self):
        names = [f"WR_{index:02d}" for index in range(1, 16)]
        self.assertEqual(device_list_text(names), "15 Wechselrichter")
        self.assertEqual(device_list_text(["WR_13", "WR_14"]), "WR_13, WR_14")


class DeviceListTest(unittest.TestCase):
    def test_query_pages_inside_condition_params(self):
        path = device_list_path("NE=276597618", 2, 100)
        self.assertIn("conditionParams.parentDn=NE%3D276597618", path)
        self.assertIn("conditionParams.recordperpage=100", path)
        self.assertIn("conditionParams.pageNo=2", path)

    def test_later_page_is_kept_without_duplicates(self):
        pages = [
            [{"dn": "a", "name": "WR_01"}, {"dn": "b", "name": "WR_17"}],
            [{"dn": "b", "name": "WR_17"}, {"dn": "c", "name": "WR_18"}, {"dn": "d", "name": "WR_22"}],
        ]
        self.assertEqual(
            [item["name"] for item in merge_device_pages(pages)],
            ["WR_01", "WR_17", "WR_18", "WR_22"],
        )


class PlantSelectTest(unittest.TestCase):
    def test_matches_name_or_dn(self):
        plants = [{"name": "Allach Huberhof", "dn": "NE=1"}, {"name": "Andere", "dn": "NE=2"}]
        self.assertEqual(len(select_plants(plants, "huber")), 1)
        self.assertEqual(select_plants(plants, "NE=2")[0]["name"], "Andere")
        self.assertEqual(len(select_plants(plants, "")), 2)


class SourceVersionTest(unittest.TestCase):
    def test_hash_is_sha256_of_the_program_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.py"
            payload = b"print('version')\n"
            path.write_bytes(payload)
            self.assertEqual(source_version(path), hashlib.sha256(payload).hexdigest())

    def test_footer_names_the_hash_as_version(self):
        created = datetime(2026, 11, 8, 17, 17, tzinfo=BERLIN)
        digest = "ab" * 32
        _left, _center, note, right, identity = footer_parts("Allach", created, 2, 9, digest)
        self.assertEqual(note, "Gerätedaten, Netzverhalten und Stringkonfiguration")
        self.assertEqual(right, "Seite 2 von 9")
        self.assertEqual(identity, f"Erstellt mit konfigurationsbericht.py SHA-256: {digest}")
        pdf = ReportPDF(created, digest)
        pdf.set_font("DejaVu", "", 6.5)
        self.assertLess(pdf.get_string_width(identity), pdf.epw)


if __name__ == "__main__":
    unittest.main()
