# Supplied historical weather data: inspection notes

## What is present

- **Format / files:** 72 NetCDF4/HDF5 files converted from GRIB with cfgrib/ecCodes; 12 files per calendar year from 2020 through 2025. Source filenames are opaque hashes. The original NetCDF files remain unchanged under `backend/data/<year>/`.
- **Attribution:** File metadata names the European Centre for Medium-Range Weather Forecasts (ECMWF). It does not establish whether the product is ERA5, ERA5-Land, or another product, nor provide a source URL or licence. MAUSAM therefore records the exact product/version as unknown.
- **Variables and source units:** `t2m` 2 m temperature (K); `d2m` 2 m dewpoint (K); `swvl1` volumetric soil water layer 1 (`m**3 m**-3`); `u10` and `v10` 10 m wind components (`m s**-1`); `sp` surface pressure (Pa). Each uses `valid_time`, `latitude`, `longitude` dimensions.
- **Dimensions:** Each monthly file has `(valid_time, latitude, longitude)` data variables, with the time dimension 28–31, latitude 24 and longitude 22; each daily slice has 528 grid cells.
- **Grid:** Regular latitude/longitude, 0.1° × 0.1°. Coordinate centres span 24.9–27.2°N and 84.2–86.3°E; latitude is stored descending. Grid cells are intersected against the supplied verified subdistrict polygons. Subdistrict means are area-weighted over those intersections, with a cosine-latitude area correction.
- **Time:** One instantaneous sample per calendar date at 10:00 UTC (15:30 IST), not hourly or sub-daily. Source coverage is 2020-01-01 through 2025-12-31: 2,186 unique dates of 2,192 calendar days. The six absent dates are 2024-03-30, 2024-03-31, 2024-04-30, 2024-05-30, 2024-05-31, and 2024-06-30. No duplicate timestamps were found.
- **Missing values / observed ranges:** No non-finite source values were found (0 of 1,154,208 values for each of the six variables). Observed source ranges: `t2m` 283.095–319.470 K; `d2m` 268.098–301.473 K; `swvl1` 0.0994–0.4390 m³/m³; `u10` −10.249–10.312 m/s; `v10` −5.695–5.815 m/s; `sp` 87,314–101,883 Pa. Date gaps are retained as missing output days, never filled with zero or interpolated values.

## Processing decisions and limits

The supported location scope is the verified subdistrict set inside Purba/East Champaran (LGD 213), Muzaffarpur (208), and Patna (212), taken from `backend/data/geography/mausam/bihar/`. This is 66 subdistrict polygons. Data outside these polygons is not assigned to a city or nearest district.

Temperature is converted from kelvin to °C. Wind speed is computed per grid cell as `sqrt(u10² + v10²)` and then area-averaged; pressure remains in Pa and soil-water units are preserved. The CSV includes a row for each subdistrict and each date across the full 2020-01-01–2025-12-31 calendar. Missing source dates have null weather values. Rolling means require all 7, 14, or 30 daily inputs. Temperature snapshot rolling maxima are maxima of daily 10:00 UTC spatial-mean snapshots, not intraday high temperatures. Because the dataset contains one instantaneous sample per day, `temperature_min_c`, `temperature_max_c`, and `wind_speed_max_ms` remain null; this is intentional.

Anomaly columns compare each date to its available 2020–2025 same-calendar-day values; they are withheld unless at least three baseline samples are available. This is not a 30-year climatology. There is no precipitation variable, so this pipeline produces no rainfall, rainfall anomaly, dry-spell, or rainfall-prediction target.

## Outputs and database

`process_weather_dataset.py` writes a database-ready daily CSV, a JSON manifest, and a small byte-offset index for efficient local API reads by subdistrict. It can optionally batch-upsert into `weather_daily`, using the existing `locations` and `data_sources` tables. The unique key `(location_id, date, source_id)` makes repeat imports idempotent. Migration `003_weather_daily.sql` adds this table; it does not replace `weather_observations` or store raw files in Postgres. The weather API serves the local CSV until database rows are imported; once configured, it prefers database rows. Admin data status reports database counts/coverage after the migration and import have actually run.

See the MAUSAM README section “Historical weather processing” for exact commands. Actual database import requires the migration, all 66 verified subdistrict location rows, and a configured `DATABASE_URL`; generating the local CSV does not imply those database steps have happened.
