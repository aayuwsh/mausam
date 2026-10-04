import asyncio
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import xarray as xr

from backend.app.process_weather_dataset import (
    add_features, aggregate_source, batch_upsert, build_csv_index, build_spatial_weights,
    deduplicate, inspect_dataset, to_celsius, validate_records, weighted_mean, wind_speed,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend/data"
GEOGRAPHY = DATA / "geography/mausam/bihar"


class WeatherPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = inspect_dataset(DATA)

    def test_01_netcdf_dataset_loading(self):
        sample = next(DATA.rglob("*.nc"))
        with xr.open_dataset(sample, engine="h5netcdf") as ds:
            self.assertEqual(ds.sizes["latitude"], 24)
            self.assertEqual(ds.sizes["longitude"], 22)
            self.assertIn("valid_time", ds.coords)

    def test_02_variable_detection(self):
        self.assertEqual(set(self.audit["variables"]), {"d2m", "t2m", "swvl1", "u10", "v10", "sp"})
        self.assertEqual(self.audit["variables"]["t2m"]["units"], "K")

    def test_03_unit_conversion(self):
        np.testing.assert_allclose(to_celsius([273.15, 300], "K"), [0, 26.85])
        with self.assertRaises(ValueError):
            to_celsius([1], "unknown")

    def test_04_wind_speed_calculation(self):
        np.testing.assert_allclose(wind_speed([3, 0], [4, -2]), [5, 2])

    def test_05_daily_spatial_weighted_aggregation_primitive(self):
        self.assertAlmostEqual(weighted_mean([2, 8], [1, 3]), 6.5)
        self.assertIsNone(weighted_mean([np.nan], [1]))

    def test_06_missing_values_and_calendar_gaps(self):
        self.assertTrue(all(value == 0 for value in self.audit["nonfinite_source_values"].values()))
        self.assertEqual(len(self.audit["missing_dates"]), 6)
        self.assertIn("2024-03-30", self.audit["missing_dates"])

    def test_07_polygon_spatial_filtering(self):
        with xr.open_dataset(next(DATA.rglob("*.nc")), engine="h5netcdf") as ds:
            locations = build_spatial_weights(GEOGRAPHY, ds.latitude.values, ds.longitude.values)
        self.assertEqual(len(locations), 66)
        self.assertEqual({x["district_code"] for x in locations.values()}, {"213", "208", "212"})
        self.assertTrue(all(len(x["indices"]) > 0 for x in locations.values()))

    def test_08_out_of_scope_district_rejected(self):
        from backend.app.process_weather_dataset import _loc_feature
        feature = json.loads((GEOGRAPHY / "subdistricts.geojson").read_text())["features"][0]
        feature["properties"]["dist_lgd"] = 999
        with self.assertRaises(ValueError):
            _loc_feature(feature)

    def test_09_duplicate_records_rejected(self):
        row = {"location_id": "IN-BR-S-1", "date": "2020-01-01"}
        with self.assertRaises(ValueError):
            deduplicate([row, row.copy()])

    def test_10_batched_idempotent_database_upsert_preparation(self):
        class FakeConnection:
            def __init__(self):
                self.batches = []
            async def executemany(self, query, rows):
                self.batches.append((query, rows))

        conn = FakeConnection()
        rows = [{"location_id": f"IN-BR-S-{i}", "date": f"2020-01-0{i}", "value": i} for i in range(1, 4)]
        query = "INSERT INTO weather_daily ... ON CONFLICT(location_id,date,source_id) DO UPDATE"
        total = asyncio.run(batch_upsert(conn, query, rows, ["location_id", "date", "value"], "source", 2))
        self.assertEqual(total, 3)
        self.assertEqual([len(batch[1]) for batch in conn.batches], [2, 1])
        self.assertIsInstance(conn.batches[0][1][0][1], date)
        self.assertIn("ON CONFLICT", conn.batches[0][0])

    def test_11_rolling_features_and_missing_day(self):
        sample = next(DATA.rglob("*.nc"))
        with xr.open_dataset(sample, engine="h5netcdf") as ds:
            spatial = build_spatial_weights(GEOGRAPHY, ds.latitude.values, ds.longitude.values)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            shutil.copy2(sample, folder / sample.name)
            rows, _ = aggregate_source(folder, spatial)
        location_id = sorted(spatial)[0]
        rows = sorted((row for row in rows if row["location_id"] == location_id), key=lambda row: row["date"])
        start, end = rows[0]["date"], rows[-1]["date"]
        rows.pop(20)
        result = add_features(rows, start, end)
        self.assertEqual(len(result), 31)
        self.assertIsNone(result[20]["temperature_mean_c"])
        self.assertIsNone(result[20]["temperature_snapshot_7d_mean"])
        self.assertIsNotNone(result[6]["temperature_snapshot_7d_mean"])

    def test_12_date_range_validation(self):
        rows = [{"location_id": "IN-BR-S-1", "date": d}
                for d in ("2020-01-01", "2020-01-02")]
        report = validate_records(rows, {"IN-BR-S-1"}, "2020-01-01", "2020-01-02")
        self.assertEqual(report["records"], 2)
        self.assertFalse(report["rainfall_columns_created"])

    def test_13_month_file_aggregate_smoke_test(self):
        sample = next(DATA.rglob("*.nc"))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            shutil.copy2(sample, folder / sample.name)
            with xr.open_dataset(sample, engine="h5netcdf") as ds:
                spatial = build_spatial_weights(GEOGRAPHY, ds.latitude.values, ds.longitude.values)
            rows, quality = aggregate_source(folder, spatial)
        self.assertEqual(len(rows), 31 * 66)
        self.assertEqual(quality["source_nonfinite_values"], {"d2m": 0, "swvl1": 0, "v10": 0, "t2m": 0, "u10": 0, "sp": 0})
        self.assertTrue(all(row["temperature_min_c"] is None for row in rows))

    def test_14_local_csv_index_and_history_serving(self):
        from backend.app.main import local_history, local_weather_index
        self.assertIsNotNone(local_weather_index())
        rows = local_history("IN-BR-S-1031", 7)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["date"], "2025-12-31")
        self.assertIsInstance(rows[0]["temperature_mean_c"], float)

    def test_15_historical_api_returns_real_local_observations(self):
        from backend.app.main import app, historical_weather
        app.state.db = None
        payload = asyncio.run(historical_weather("IN-BR-S-1031", 7))
        self.assertEqual(payload["status"], "available")
        self.assertEqual(len(payload["records"]), 7)
        self.assertFalse(payload["rainfall_available"])


if __name__ == "__main__":
    unittest.main()
