"""Inspect, spatially aggregate, feature and optionally import MAUSAM NetCDF data.

The source has one instantaneous observation per date, so this pipeline never
labels that snapshot as a true intraday minimum or maximum.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Polygon, shape


SUPPORTED_DISTRICTS = {"213", "208", "212"}
EXPECTED_VARIABLES = {"d2m", "t2m", "swvl1", "u10", "v10", "sp"}
OUT_FIELDS = [
    "temperature_mean_c", "temperature_min_c", "temperature_max_c",
    "dewpoint_temperature_c", "soil_moisture_mean", "wind_speed_mean_ms",
    "wind_speed_max_ms", "surface_pressure_pa",
]
WINDOW_VARIABLES = {
    "temperature_mean_c": ("temperature_mean_c", "temperature_snapshot"),
    "dewpoint_temperature_c": ("dewpoint_temperature_c", "dewpoint"),
    "soil_moisture_mean": ("soil_moisture_mean", "soil_moisture"),
    "wind_speed_mean_ms": ("wind_speed_mean_ms", "wind_speed"),
    "surface_pressure_pa": ("surface_pressure_pa", "pressure"),
}
def to_celsius(values: Any, units: str) -> np.ndarray:
    """Convert source temperature values only for recognized unit metadata."""
    unit = units.strip().lower()
    values = np.asarray(values, dtype=float)
    if unit in {"k", "kelvin"}:
        return values - 273.15
    if unit in {"c", "°c", "degc", "degrees_celsius", "degree_celsius"}:
        return values
    raise ValueError(f"Unsupported temperature units: {units!r}")


def wind_speed(u: Any, v: Any) -> np.ndarray:
    """Compute wind speed from matching u/v component arrays in source units."""
    u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    if u.shape != v.shape:
        raise ValueError("u and v wind component arrays must have matching shapes")
    return np.hypot(u, v)


def weighted_mean(values: Any, weights: Any) -> float | None:
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return None
    return float(np.average(values[valid], weights=weights[valid]))


def deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject duplicates rather than silently choosing one source row."""
    rows = list(records)
    keys = [(str(row["location_id"]), str(row["date"])) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate (location_id, date) records found")
    return rows


def _loc_feature(feature: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
    props = feature.get("properties") or {}
    code = str(props.get("lgd", ""))
    district_code = str(props.get("dist_lgd", ""))
    location_id = str(props.get("id", ""))
    if not location_id.startswith("IN-BR-S-") or not code or district_code not in SUPPORTED_DISTRICTS:
        raise ValueError(f"Out-of-scope or invalid subdistrict feature: {props}")
    geom = shape(feature["geometry"])
    if geom.is_empty or not geom.is_valid or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"Invalid subdistrict geometry: {location_id}")
    return location_id, props, geom


def build_spatial_weights(geography: Path, latitudes: np.ndarray, longitudes: np.ndarray):
    """Return polygon/cell intersection weights for the supplied subdistricts.

    Weights use intersection area in lon/lat degrees with cosine latitude
    correction, adequate for relative area weights over this compact region.
    Grid coordinates are cell centres; cell edges are derived from verified
    regular-grid spacing, then clipped by each actual administrative polygon.
    """
    collection = json.loads((geography / "subdistricts.geojson").read_text(encoding="utf-8"))
    districts = json.loads((geography / "districts.geojson").read_text(encoding="utf-8"))["features"]
    district_codes = {str(f["properties"].get("lgd")) for f in districts}
    if district_codes != SUPPORTED_DISTRICTS:
        raise ValueError(f"Expected the three supported districts only, got {district_codes}")
    features = collection.get("features", [])
    if not features:
        raise ValueError("No subdistrict polygons were provided")
    lat_step = float(np.median(np.abs(np.diff(latitudes))))
    lon_step = float(np.median(np.abs(np.diff(longitudes))))
    if not np.allclose(np.abs(np.diff(latitudes)), lat_step) or not np.allclose(np.diff(longitudes), lon_step):
        raise ValueError("Only a regular latitude/longitude grid is supported")
    centres = [(float(lat), float(lon)) for lat in latitudes for lon in longitudes]
    cells = [Polygon([(lon-lon_step/2, lat-lat_step/2), (lon+lon_step/2, lat-lat_step/2),
                      (lon+lon_step/2, lat+lat_step/2), (lon-lon_step/2, lat+lat_step/2)])
             for lat, lon in centres]
    result: dict[str, dict[str, Any]] = {}
    for feature in features:
        location_id, props, geom = _loc_feature(feature)
        indices, weights = [], []
        for i, cell in enumerate(cells):
            if geom.intersects(cell):
                area = geom.intersection(cell).area
                if area > 0:
                    indices.append(i)
                    weights.append(area * math.cos(math.radians(centres[i][0])))
        if not indices:
            raise ValueError(f"Subdistrict has no intersecting source grid cells: {location_id}")
        result[location_id] = {"name": props.get("name"), "district_code": str(props.get("dist_lgd")),
                               "indices": np.asarray(indices), "weights": np.asarray(weights),
                               "grid_cells": len(indices)}
    if len(result) != len(features) or len(result) != 66:
        raise ValueError(f"Expected 66 unique supported subdistricts; found {len(result)}")
    return result


def inspect_dataset(input_dir: Path) -> dict[str, Any]:
    files = sorted(input_dir.rglob("*.nc"))
    if not files:
        raise ValueError(f"No NetCDF files found under {input_dir}")
    timestamps: list[np.datetime64] = []
    missing: Counter[str] = Counter()
    ranges: dict[str, list[float | None]] = {name: [None, None] for name in EXPECTED_VARIABLES}
    variable_attrs: dict[str, dict[str, Any]] = {}
    resolutions: set[tuple[float, float]] = set()
    coord_bounds: list[tuple[float, float, float, float]] = []
    for path in files:
        with xr.open_dataset(path, engine="h5netcdf", decode_times=True) as ds:
            if set(ds.data_vars) != EXPECTED_VARIABLES:
                raise ValueError(f"Variable mismatch in {path}: {sorted(ds.data_vars)}")
            for coord in ("latitude", "longitude", "valid_time"):
                if coord not in ds.coords:
                    raise ValueError(f"Required coordinate {coord!r} is missing from {path}")
            if ds.valid_time.ndim != 1 or ds.latitude.ndim != 1 or ds.longitude.ndim != 1:
                raise ValueError(f"Expected one-dimensional regular coordinates in {path}")
            resolutions.add((round(float(np.median(np.abs(np.diff(ds.latitude.values)))), 8),
                             round(float(np.median(np.diff(ds.longitude.values))), 8)))
            coord_bounds.append((float(ds.latitude.min()), float(ds.latitude.max()),
                                 float(ds.longitude.min()), float(ds.longitude.max())))
            timestamps.extend(np.asarray(ds.valid_time.values, dtype="datetime64[ns]"))
            for var in EXPECTED_VARIABLES:
                attrs = ds[var].attrs
                variable_attrs[var] = {"long_name": attrs.get("long_name"), "units": attrs.get("units"),
                                       "grib_short_name": attrs.get("GRIB_shortName"),
                                       "grib_data_type": attrs.get("GRIB_dataType"),
                                       "grib_step_type": attrs.get("GRIB_stepType")}
                vals = np.asarray(ds[var].values)
                # NaNs are the NetCDF _FillValue representation in these files.
                finite = vals[np.isfinite(vals)]
                missing[var] += int(vals.size - finite.size)
                if finite.size:
                    ranges[var][0] = float(finite.min()) if ranges[var][0] is None else min(ranges[var][0], float(finite.min()))
                    ranges[var][1] = float(finite.max()) if ranges[var][1] is None else max(ranges[var][1], float(finite.max()))
    unique = sorted(set(timestamps))
    if len(timestamps) != len(unique):
        raise ValueError("Duplicate source timestamps found across files")
    days = np.asarray(unique, dtype="datetime64[ns]").astype("datetime64[D]")
    expected = np.arange(days.min(), days.max() + np.timedelta64(1, "D"), dtype="datetime64[D]")
    absent = np.setdiff1d(expected, days)
    if len(resolutions) != 1:
        raise ValueError(f"Inconsistent spatial resolutions: {resolutions}")
    return {"file_count": len(files), "timestamp_count": len(unique), "time_start": str(days.min()),
            "time_end": str(days.max()), "dates_present": len(days), "calendar_dates": len(expected),
            "missing_dates": [str(d) for d in absent], "resolution_degrees": list(next(iter(resolutions))),
            "latitude_bounds": [min(x[0] for x in coord_bounds), max(x[1] for x in coord_bounds)],
            "longitude_bounds": [min(x[2] for x in coord_bounds), max(x[3] for x in coord_bounds)],
            "samples_per_day": 1, "variables": variable_attrs, "nonfinite_source_values": dict(missing),
            "source_value_ranges": ranges,
            "timestamp_hour_utc": sorted({int(pd.Timestamp(t).hour) for t in unique}),
            "dataset_attribution": "ECMWF attribution in NetCDF metadata; exact product/version not stated"}


def _check_physical_values(name: str, values: np.ndarray, units: str | None = None) -> None:
    limits = {"t2m": (-90, 65), "d2m": (-100, 60), "swvl1": (0, 1),
              "u10": (-120, 120), "v10": (-120, 120), "sp": (30_000, 120_000)}
    finite = values[np.isfinite(values)]
    if name in {"t2m", "d2m"}:
        finite = to_celsius(finite, units or "")
    if finite.size and (finite.min() < limits[name][0] or finite.max() > limits[name][1]):
        raise ValueError(f"Physically implausible values in {name}: {finite.min()}..{finite.max()}")


def aggregate_source(input_dir: Path, spatial: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = []
    missing = Counter()
    dates_seen: set[str] = set()
    for path in sorted(input_dir.rglob("*.nc")):
        with xr.open_dataset(path, engine="h5netcdf", decode_times=True) as ds:
            arrays = {name: np.asarray(ds[name].values, dtype=float) for name in EXPECTED_VARIABLES}
            for name, arr in arrays.items():
                _check_physical_values(name, arr, ds[name].attrs.get("units"))
                missing[name] += int((~np.isfinite(arr)).sum())
            dates = ds.valid_time.values.astype("datetime64[D]")
            if len(set(dates.tolist())) != len(dates):
                raise ValueError(f"Duplicate daily timestamp inside {path}")
            lat_count, lon_count = ds.sizes["latitude"], ds.sizes["longitude"]
            flat = {name: arr.reshape((len(dates), lat_count * lon_count)) for name, arr in arrays.items()}
            for t_index, day in enumerate(dates):
                day_string = str(day)
                if day_string in dates_seen:
                    raise ValueError(f"Duplicate source date across files: {day_string}")
                dates_seen.add(day_string)
                temp = to_celsius(flat["t2m"][t_index], ds.t2m.attrs["units"])
                dew = to_celsius(flat["d2m"][t_index], ds.d2m.attrs["units"])
                speed = wind_speed(flat["u10"][t_index], flat["v10"][t_index])
                vals = {"temperature_mean_c": temp, "dewpoint_temperature_c": dew,
                        "soil_moisture_mean": flat["swvl1"][t_index], "wind_speed_mean_ms": speed,
                        "surface_pressure_pa": flat["sp"][t_index]}
                for location_id, area in spatial.items():
                    ix, weights = area["indices"], area["weights"]
                    row = {"location_id": location_id, "date": day_string,
                           "temperature_min_c": None, "temperature_max_c": None,
                           "wind_speed_max_ms": None, "spatial_grid_cells": int(len(ix))}
                    for field, all_values in vals.items():
                        row[field] = weighted_mean(all_values[ix], weights)
                    records.append(row)
    return deduplicate(records), {"source_nonfinite_values": dict(missing), "source_dates": len(dates_seen)}


def add_features(records: list[dict[str, Any]], start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """Pad true missing calendar dates and calculate rolling snapshot features."""
    frame = pd.DataFrame(deduplicate(records))
    if frame.empty:
        return []
    frame["date"] = pd.to_datetime(frame["date"])
    start_date = pd.Timestamp(start or frame.date.min())
    end_date = pd.Timestamp(end or frame.date.max())
    all_dates = pd.date_range(start_date, end_date, freq="D")
    expanded = []
    for location_id, group in frame.groupby("location_id", sort=True):
        group = group.set_index("date").sort_index()
        if group.index.has_duplicates:
            raise ValueError(f"Duplicate daily data for {location_id}")
        group = group.reindex(all_dates)
        group["location_id"] = location_id
        for base, stem in [("temperature_mean_c", "temperature_snapshot"),
                           ("dewpoint_temperature_c", "dewpoint"),
                           ("soil_moisture_mean", "soil_moisture"),
                           ("wind_speed_mean_ms", "wind_speed"),
                           ("surface_pressure_pa", "pressure")]:
            for window in (7, 14, 30):
                group[f"{stem}_{window}d_mean"] = group[base].rolling(window, min_periods=window).mean()
                if stem == "temperature_snapshot":
                    group[f"{stem}_{window}d_max_c"] = group[base].rolling(window, min_periods=window).max()
        baseline_fields = [("temperature_mean_c", "temperature_anomaly_c"),
                           ("dewpoint_temperature_c", "dewpoint_anomaly_c"),
                           ("soil_moisture_mean", "soil_moisture_anomaly"),
                           ("surface_pressure_pa", "pressure_anomaly_pa")]
        group["_month_day"] = group.index.strftime("%m-%d")
        baseline_sample_counts = None
        for base, anomaly in baseline_fields:
            # The baseline is strictly the available 2020-2025 source period.
            history = group.loc[(group.index.year >= 2020) & (group.index.year <= 2025), base]
            climatology = history.groupby(history.index.strftime("%m-%d")).agg(["mean", "count"])
            keys = group.index.strftime("%m-%d")
            means = pd.Series(keys, index=group.index).map(climatology["mean"])
            counts = pd.Series(keys, index=group.index).map(climatology["count"]).fillna(0)
            group[anomaly] = (group[base] - means).where(counts >= 3)
            if baseline_sample_counts is None:
                baseline_sample_counts = counts
        # Number of years with an available same-calendar-day baseline sample.
        group["baseline_years"] = baseline_sample_counts.astype("int64") if baseline_sample_counts is not None else 0
        group["spatial_grid_cells"] = group["spatial_grid_cells"].ffill().bfill()
        group["date"] = group.index.date.astype(str)
        expanded.append(group.drop(columns=["_month_day"]).reset_index(drop=True))
    result = pd.concat(expanded, ignore_index=True).replace({np.nan: None})
    desired = ["location_id", "date", *OUT_FIELDS]
    desired += [f"{stem}_{window}d_mean" for stem in ("temperature_snapshot", "dewpoint", "soil_moisture", "wind_speed", "pressure") for window in (7, 14, 30)]
    desired += [f"temperature_snapshot_{window}d_max_c" for window in (7, 14, 30)]
    desired += ["temperature_anomaly_c", "dewpoint_anomaly_c", "soil_moisture_anomaly", "pressure_anomaly_pa", "baseline_years", "spatial_grid_cells"]
    rows = result[desired].to_dict(orient="records")
    for row in rows:
        for field in ("baseline_years", "spatial_grid_cells"):
            if row.get(field) is not None:
                row[field] = int(row[field])
    return rows


def validate_records(records: list[dict[str, Any]], expected_location_ids: set[str], start: str, end: str) -> dict[str, Any]:
    rows = deduplicate(records)
    if not rows:
        raise ValueError("No processed records were generated")
    ids = {row["location_id"] for row in rows}
    if ids != expected_location_ids:
        raise ValueError(f"Processed location mismatch: expected {len(expected_location_ids)}, found {len(ids)}")
    dates = {str(row["date"]) for row in rows}
    expected_dates = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
    missing_dates = sorted(set(expected_dates) - dates)
    if missing_dates:
        raise ValueError(f"Processed calendar is missing padded dates: {missing_dates}")
    null_counts = {field: sum(row.get(field) is None for row in rows) for field in OUT_FIELDS}
    return {"records": len(rows), "locations": len(ids), "date_start": min(dates), "date_end": max(dates),
            "missing_calendar_dates": missing_dates, "null_counts": null_counts,
            "unavailable_intraday_extrema": ["temperature_min_c", "temperature_max_c", "wind_speed_max_ms"],
            "rainfall_columns_created": False}


def write_csv(records: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(records[0]) if records else []
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)
    build_csv_index(output)


def build_csv_index(output: Path) -> dict[str, Any]:
    """Index contiguous location blocks so the API can seek to a farm's CSV rows."""
    index: dict[str, dict[str, int]] = {}
    with output.open("r", newline="", encoding="utf-8") as handle:
        columns = next(csv.reader([handle.readline()]))
        location_index = columns.index("location_id")
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            row = next(csv.reader([line]))
            location_id = row[location_index]
            if location_id not in index:
                index[location_id] = {"offset": offset, "count": 0}
            index[location_id]["count"] += 1
    data = {"columns": columns, "locations": index}
    output.with_suffix(".index.json").write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    return data


async def import_to_database(records: list[dict[str, Any]], manifest: dict[str, Any], database_url: str) -> None:
    """Idempotent batched upsert; migration 003 and verified locations are required."""
    from .db_connection import connect_database
    source_id = "ecmwf_user_netcdf_2020_2025"
    conn = await connect_database(database_url)
    try:
        await conn.execute("""INSERT INTO data_sources(id,source_name,source_url,dataset_name,licence,configured,last_status,last_processed_at,details)
          VALUES($1,$2,$3,$4,$5,true,'processed',now(),$6::jsonb)
          ON CONFLICT(id) DO UPDATE SET configured=true,last_processed_at=now(),last_status='processed',details=EXCLUDED.details""",
          source_id, "ECMWF (as attributed in supplied files)", "user-provided local NetCDF; upstream URL not supplied",
          "Six-variable daily ECMWF NetCDF (exact product/version not stated)", None, json.dumps(manifest))
        columns = ["location_id", "date", *OUT_FIELDS,
                   *[f"{stem}_{window}d_mean" for stem in ("temperature_snapshot", "dewpoint", "soil_moisture", "wind_speed", "pressure") for window in (7, 14, 30)],
                   *[f"temperature_snapshot_{window}d_max_c" for window in (7, 14, 30)],
                   "temperature_anomaly_c", "dewpoint_anomaly_c", "soil_moisture_anomaly", "pressure_anomaly_pa", "baseline_years", "spatial_grid_cells"]
        sql_columns = ",".join(columns + ["source_id", "created_at"])
        params = ",".join(f"${i}" for i in range(1, len(columns)+1)) + f",${len(columns)+1},now()"
        updates = ",".join(f"{col}=EXCLUDED.{col}" for col in columns if col not in {"location_id", "date"})
        query = f"INSERT INTO weather_daily({sql_columns}) VALUES({params}) ON CONFLICT(location_id,date,source_id) DO UPDATE SET {updates}"
        async with conn.transaction():
            await batch_upsert(conn, query, records, columns, source_id, batch_size=1000)
    finally:
        await conn.close()


async def batch_upsert(conn: Any, query: str, records: list[dict[str, Any]], columns: list[str],
                       source_id: str, batch_size: int = 1000) -> int:
    """Execute idempotent prepared upserts in bounded batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    rows = deduplicate(records)
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        params = [tuple(date.fromisoformat(row["date"]) if column == "date" else row.get(column)
                        for column in columns) + (source_id,) for row in batch]
        await conn.executemany(query, params)
    return len(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_dir, geography = Path(args.input), Path(args.geography)
    audit = inspect_dataset(input_dir)
    first = next(iter(sorted(input_dir.rglob("*.nc"))))
    with xr.open_dataset(first, engine="h5netcdf") as ds:
        spatial = build_spatial_weights(geography, ds.latitude.values, ds.longitude.values)
    base_records, quality = aggregate_source(input_dir, spatial)
    start, end = audit["time_start"], audit["time_end"]
    records = add_features(base_records, start, end)
    quality_report = validate_records(records, set(spatial), start, end)
    manifest = {"dataset": audit["dataset_attribution"], "source_period": f"{start} through {end}",
                "spatial_bbox": {"north": audit["latitude_bounds"][1], "south": audit["latitude_bounds"][0],
                                 "west": audit["longitude_bounds"][0], "east": audit["longitude_bounds"][1]},
                "resolution_degrees": audit["resolution_degrees"], "temporal_resolution": "one instantaneous daily sample at 10:00 UTC",
                "variables": audit["variables"], "source_files": audit["file_count"], "missing_dates": audit["missing_dates"],
                "source_nonfinite_values": quality["source_nonfinite_values"], "processing_method": "Daily raster-grid cell/polygon intersection area weighted means over verified Bihar subdistrict polygons; grid cell weights corrected by cosine(latitude). Kelvin temperatures converted to Celsius; wind speed is hypot(u10,v10); pressure remains Pa; soil moisture retains m^3 m^-3. One snapshot per date, so intraday extrema are unavailable.",
                "location_count": len(spatial), "district_lgd_codes": sorted(SUPPORTED_DISTRICTS), "processed": quality_report,
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "limitations": ["Supplied files attribute data to ECMWF but do not state the exact product/version or upstream source URL.", "No precipitation variable exists; no rainfall records/features are produced.", "One instant per day cannot yield true daily temperature or wind minima/maxima.", "Daily sample timestamp is 10:00 UTC (15:30 India Standard Time).", "Historical baseline uses available 2020–2025 records, not a 30-year climatology."]}
    output = Path(args.output)
    write_csv(records, output)
    manifest["local_api_index"] = str(output.with_suffix(".index.json"))
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    if args.database_url:
        asyncio.run(import_to_database(records, manifest, args.database_url))
    return {"dataset_audit": audit, "processing_quality": quality, "processed_validation": quality_report,
            "output_csv": str(output), "manifest": str(manifest_path), "database_imported": bool(args.database_url)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("backend/data"))
    parser.add_argument("--geography", type=Path, default=Path("backend/data/geography/mausam/bihar"))
    parser.add_argument("--output", type=Path, default=Path("backend/data/processed/weather_daily.csv"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
