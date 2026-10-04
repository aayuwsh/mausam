-- Extend the existing weather_daily/climate_indices tables for the supplied
-- IMD + ERA5 + NOAA inputs. Snapshot fields stay distinct from daily statistics.
ALTER TABLE weather_daily
  ADD COLUMN IF NOT EXISTS rainfall_mm double precision,
  ADD COLUMN IF NOT EXISTS rainfall_3d_mm double precision,
  ADD COLUMN IF NOT EXISTS rainfall_7d_mm double precision,
  ADD COLUMN IF NOT EXISTS rainfall_14d_mm double precision,
  ADD COLUMN IF NOT EXISTS rainfall_30d_mm double precision,
  ADD COLUMN IF NOT EXISTS rainfall_anomaly_mm double precision,
  ADD COLUMN IF NOT EXISTS consecutive_dry_days integer,
  ADD COLUMN IF NOT EXISTS temperature_snapshot_10utc_c double precision,
  ADD COLUMN IF NOT EXISTS dewpoint_snapshot_10utc_c double precision,
  ADD COLUMN IF NOT EXISTS surface_pressure_snapshot_10utc_pa double precision,
  ADD COLUMN IF NOT EXISTS soil_moisture_layer1 double precision,
  ADD COLUMN IF NOT EXISTS soil_moisture_layer2 double precision,
  ADD COLUMN IF NOT EXISTS wind_u_mean_ms double precision,
  ADD COLUMN IF NOT EXISTS wind_v_mean_ms double precision,
  ADD COLUMN IF NOT EXISTS oni double precision,
  ADD COLUMN IF NOT EXISTS dmi double precision,
  ADD COLUMN IF NOT EXISTS dmi_3m double precision,
  ADD COLUMN IF NOT EXISTS target_rain_7d_mm double precision,
  ADD COLUMN IF NOT EXISTS target_rain_14d_mm double precision,
  ADD COLUMN IF NOT EXISTS target_rain_21d_mm double precision,
  ADD COLUMN IF NOT EXISTS target_rain_30d_mm double precision,
  ADD COLUMN IF NOT EXISTS observed_heavy_rain_label boolean,
  ADD COLUMN IF NOT EXISTS observed_dry_spell_label boolean;

INSERT INTO data_sources(id, source_name, source_url, dataset_name, licence, configured, last_status, details)
VALUES
 ('imd_user_gridded_rainfall_2020_2025','India Meteorological Department (user-provided files)','User-provided local NetCDF; upstream URL/licence metadata not supplied','Daily gridded rainfall 2020–2025',NULL,false,'pending',jsonb_build_object('original_files','data/raw/imd_rainfall','processing','area-weighted intersection of source grid cells with supported administrative polygons','units','mm')),
 ('era5_user_land_2020_2025','ECMWF attribution in supplied files','User-provided local NetCDF; exact product/version and upstream URL not supplied','Supplied ERA5/ERA5-Land-labelled daily snapshots 2020–2025',NULL,false,'pending',jsonb_build_object('original_files','data/raw/era5','temporal_resolution','one timestamp at 10:00 UTC per day','processing','area-weighted intersection; snapshot values not daily mean/min/max')),
 ('noaa_oni_user_file','NOAA PSL (as described in supplied file)','User-provided local NetCDF; upstream URL/licence metadata not supplied','Oceanic Niño Index',NULL,false,'pending',jsonb_build_object('original_file','data/raw/climate_indices/oni.nc','temporal_resolution','monthly 3-month running mean')),
 ('dmi_hadisst_user_file','HadISST DMI (as described in supplied file)','User-provided local CSV; upstream URL/licence metadata not supplied','Dipole Mode Index',NULL,false,'pending',jsonb_build_object('original_file','data/raw/climate_indices/dmi.had.long.csv','temporal_resolution','monthly'))
 ,('mausam_supplied_multisource_2020_2025','MAUSAM supplied multi-source observations','User-provided local files; individual source provenance is stored in details','IMD rainfall + supplied ERA daily snapshots + monthly ONI/DMI',NULL,false,'pending',jsonb_build_object('rainfall_source','imd_user_gridded_rainfall_2020_2025','era_source','era5_user_land_2020_2025','processing','area-weighted polygon intersections; local pipeline output'))
ON CONFLICT(id) DO UPDATE SET dataset_name=EXCLUDED.dataset_name, details=EXCLUDED.details;

CREATE INDEX IF NOT EXISTS weather_daily_rainfall_date_idx ON weather_daily(date) WHERE rainfall_mm IS NOT NULL;
