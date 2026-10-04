import asyncio
import csv
import json
import unittest
from datetime import date
from pathlib import Path

from backend.app.extract_crop_calendar import CORE, parse_window, validate
from backend.app.import_crop_calendar import CALENDAR_UPSERT_SQL, crop_id, source_reference
from backend.app.main import app, crop_calendar, crop_calendar_phase, local_crop_calendar, _calendar_interval, local_district_for_location, live_location_weather, supported_national_locations, locations, map_locations
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/agriculture"


class CropCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (DATA / "crop_calendar_raw.csv").open(encoding="utf-8-sig") as handle:
            cls.records = list(csv.DictReader(handle))

    def test_month_and_week_window_normalization_keeps_no_year(self):
        self.assertEqual(parse_window("3rd week of May-2nd week of June"), ("May, week 3", "June, week 2"))
        self.assertEqual(parse_window("June- July"), ("June", "July"))
        self.assertEqual(parse_window("2nd week of Oct. – 2nd week of Nov."), ("October, week 2", "November, week 2"))
        self.assertEqual(parse_window("10th October – 20th October"), ("October 10", "October 20"))

    def test_records_have_source_district_and_core_fields(self):
        self.assertEqual(len(self.records), 31)
        self.assertEqual({r["district"] for r in self.records}, {"East Champaran", "Muzaffarpur", "Patna"})
        self.assertTrue(set(CORE).issubset(self.records[0]))
        self.assertTrue(all(r["source_document"] and r["source_page"] and r["source_text"] for r in self.records))

    def test_missing_harvest_and_duration_stay_null(self):
        _, report = validate(self.records)
        self.assertTrue(all(not r["harvest_start"] and not r["harvest_end"] for r in self.records))
        self.assertTrue(all(not r["duration_days"] for r in self.records))
        self.assertEqual(report["records_missing_harvest_window"], 31)
        self.assertEqual(report["records_missing_duration"], 31)

    def test_duplicate_key_retains_distinct_maize_windows_and_flags_them(self):
        rows, report = validate(self.records)
        east_maize = [r for r in rows if r["district"] == "East Champaran" and r["crop"] == "Maize" and r["season"] == "Kharif" and "Rainfed" in r["condition"]]
        self.assertEqual(len(east_maize), 2)
        self.assertNotEqual((east_maize[0]["sowing_start"], east_maize[0]["sowing_end"]), (east_maize[1]["sowing_start"], east_maize[1]["sowing_end"]))
        self.assertTrue(all("flagged_duplicate" in r["validation_status"] for r in east_maize))
        self.assertEqual(len(report["duplicate_keys"]), 1)

    def test_crop_normalization_for_database_id_is_stable(self):
        self.assertEqual(crop_id("Pigeonpea"), "pigeonpea")
        self.assertEqual(crop_id("Chickpea"), "chickpea")

    def test_import_reference_is_repeatable_and_upsert_requires_reapproval_on_changed_source(self):
        record = {"source_document": "plan.pdf", "source_page": "6", "season": "Kharif",
                  "crop": "Rice", "source_text": "Kharif Rainfed | Rice: June"}
        self.assertEqual(source_reference(record), source_reference(record.copy()))
        self.assertIn("ON CONFLICT(source_id,source_reference)", CALENDAR_UPSERT_SQL)
        self.assertIn("THEN false ELSE crop_calendar.approved END", CALENDAR_UPSERT_SQL)

    def test_calendar_phase_is_source_window_only(self):
        record = {"sowing_window_start": "June, week 2", "sowing_window_end": "June, week 3"}
        self.assertEqual(crop_calendar_phase(record, date(2025, 6, 10)), "in_sowing_window")
        self.assertEqual(crop_calendar_phase(record, date(2025, 6, 1)), "approaching_sowing_window")
        self.assertEqual(crop_calendar_phase(record, date(2025, 8, 1)), "outside_sowing_window")
        self.assertEqual(crop_calendar_phase({}, date(2025, 6, 1)), "window_unavailable")

    def test_local_calendar_lookup_and_api_resolve_subdistrict_parent(self):
        rows = local_crop_calendar("East Champaran")
        self.assertTrue(rows)
        self.assertTrue(all(row["reference_only"] for row in rows))
        self.assertIn("Rice", {row["crop"] for row in rows})
        app.state.db = None
        payload = asyncio.run(crop_calendar("IN-BR-S-1052"))
        self.assertEqual(payload["status"], "reference_only")
        self.assertEqual(payload["district"], "East Champaran")
        self.assertTrue(payload["records"])

    def test_csvs_keep_core_fields_and_traceability(self):
        with (DATA / "crop_calendar.csv").open(encoding="utf-8-sig") as handle:
            clean = list(csv.DictReader(handle))
        self.assertEqual(len(self.records), len(clean))
        self.assertTrue(set(CORE).issubset(clean[0]))
        report = json.loads((DATA / "crop_calendar_validation.json").read_text())
        self.assertEqual(report["records"], len(clean))

    def test_new_supplied_calendar_is_loaded_for_all_three_districts(self):
        with (DATA / "crop_calendar_supplied.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 30)
        self.assertEqual({r["district"] for r in rows}, {"East Champaran", "Muzaffarpur", "Patna"})
        self.assertTrue(all(r["source"] and r["source_file"] for r in rows))
        self.assertEqual(sum(bool(r["harvest_start"] and r["harvest_end"]) for r in rows), 30)
        self.assertEqual(local_district_for_location("IN-BR-B-1963"), "Patna")
        self.assertTrue(local_crop_calendar("Patna"))

    def test_invalid_calendar_harvest_date_is_withheld_and_flagged(self):
        pigeonpea = next(r for r in local_crop_calendar("Patna") if r["crop"] == "Pigeonpea" and r["season"] == "Kharif")
        self.assertIsNone(pigeonpea["harvest_end"])
        self.assertEqual(pigeonpea["source_harvest_end"], "31st Apr")
        self.assertTrue(any("Harvest dates" in warning for warning in pigeonpea["validation_warnings"]))

    def test_supplied_ordinal_calendar_labels_and_invalid_dates_are_not_guessed(self):
        self.assertEqual(_calendar_interval({"sowing_start":"1st week of July", "sowing_end":"2nd week of July"}),
                         (date(date.today().year, 7, 1), date(date.today().year, 7, 14)))
        self.assertEqual(_calendar_interval({"sowing_start":"15th Sep", "sowing_end":"30th Nov"}),
                         (date(date.today().year, 9, 15), date(date.today().year, 11, 30)))
        self.assertIsNone(_calendar_interval({"sowing_start":"1st June", "sowing_end":"31st Apr"}))

    def test_month_week_and_leap_year_boundaries_are_calendar_valid(self):
        self.assertEqual(_calendar_interval({"sowing_start":"4th week of February", "sowing_end":"February"}, 2024),
                         (date(2024, 2, 22), date(2024, 2, 29)))
        self.assertIsNone(_calendar_interval({"sowing_start":"31st Apr", "sowing_end":"May"}, 2025))

    def test_supported_national_catalog_contains_only_three_bihar_districts(self):
        rows = supported_national_locations()
        districts = [r for r in rows if r.get("level") == "district"]
        self.assertEqual({r.get("district_code") for r in districts}, {"213", "208", "212"})
        self.assertTrue(all(r.get("state_name") == "Bihar" for r in districts))
        self.assertTrue(all(r.get("level") in {"district", "block", "subdistrict"} for r in rows))
        self.assertTrue(all(r.get("state_name") == "Bihar" for r in rows))

    def test_location_api_and_map_are_scoped_to_supported_districts(self):
        districts = asyncio.run(locations("district"))["locations"]
        self.assertEqual({x["district_code"] for x in districts}, {"213", "208", "212"})
        self.assertEqual({x["properties"]["id"] for x in asyncio.run(map_locations("district"))["features"]},
                         {x["id"] for x in districts})
        patna = next(x for x in districts if x["district_code"] == "212")
        selected = asyncio.run(map_locations("block", patna["id"]))
        self.assertTrue(selected["features"])
        self.assertTrue(all("212" in f["properties"]["id"] or "212" in str(f["properties"].get("parent_id", "")) for f in selected["features"]))

    def test_block_calendar_endpoint_uses_supplied_source_rows(self):
        app.state.db = None
        response = asyncio.run(crop_calendar("IN-BR-B-1963"))
        self.assertEqual(response["district"], "Patna")
        self.assertEqual(response["status"], "reference_only")
        self.assertTrue(response["records"])
        self.assertTrue(all(row["source_file"] for row in response["records"]))

    def test_lgd_subdistrict_id_resolves_to_district_calendar(self):
        self.assertEqual(local_district_for_location("IN-LGD-S-10-208-1208"), "Muzaffarpur")
        response = asyncio.run(crop_calendar("IN-LGD-S-10-208-1208"))
        self.assertEqual(response["district"], "Muzaffarpur")
        self.assertEqual(response["status"], "reference_only")
        self.assertTrue(response["records"])

    def test_block_live_weather_requests_ten_or_more_days_from_provider(self):
        class Response:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload
        calls=[]
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, url, params):
                calls.append((url, params))
                if "seasonal-api" in url: return Response({"daily":{"time":[],"precipitation_sum":[]}})
                return Response({"timezone":"Asia/Kolkata","current":{"temperature_2m":30},"daily":{"time":[f"2026-10-{i:02d}" for i in range(1,17)],"precipitation_sum":[0]*16,"weather_code":[0]*16,"temperature_2m_min":[20]*16,"temperature_2m_max":[30]*16,"precipitation_probability_max":[0]*16}})
        with patch("httpx.AsyncClient", Client):
            result = asyncio.run(live_location_weather("IN-BR-B-1963"))
        self.assertEqual(result["location"]["level"], "block")
        self.assertEqual(result["location"]["name"], "Paliganj")
        self.assertEqual(calls[0][1]["forecast_days"], 16)
        self.assertIn("wind_direction_10m", calls[0][1]["current"])
        self.assertIn("soil_moisture_9_to_27cm", calls[0][1]["current"])
        with patch("httpx.AsyncClient", Client):
            national_result = asyncio.run(live_location_weather("IN-LGD-S-10-208-1208"))
        self.assertEqual(national_result["location"]["name"], "Katra")
        self.assertEqual(national_result["location"]["district_name"], "Muzaffarpur")

    def test_bilingual_calendar_copy_exists(self):
        text = (ROOT / "src/i18n.js").read_text(encoding="utf-8")
        for key in ("crop.calendarTitle", "crop.sowing", "crop.harvest", "crop.condition", "crop.phase.in_sowing_window", "crop.sourcePage"):
            self.assertEqual(text.count(key), 2)


if __name__ == "__main__":
    unittest.main()
