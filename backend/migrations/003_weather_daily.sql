-- Source-derived daily weather and historical features. Rainfall is intentionally
-- absent because the supplied source contains no precipitation variable.
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS last_processed_at timestamptz;

CREATE TABLE IF NOT EXISTS weather_daily (
  id bigserial PRIMARY KEY,
  location_id text NOT NULL REFERENCES locations(id),
  date date NOT NULL,
  temperature_min_c double precision,
  temperature_max_c double precision,
  temperature_mean_c double precision,
  dewpoint_temperature_c double precision,
  soil_moisture_mean double precision,
  wind_speed_mean_ms double precision,
  wind_speed_max_ms double precision,
  surface_pressure_pa double precision,
  temperature_snapshot_7d_mean double precision,
  temperature_snapshot_14d_mean double precision,
  temperature_snapshot_30d_mean double precision,
  temperature_snapshot_7d_max_c double precision,
  temperature_snapshot_14d_max_c double precision,
  temperature_snapshot_30d_max_c double precision,
  dewpoint_7d_mean double precision,
  dewpoint_14d_mean double precision,
  dewpoint_30d_mean double precision,
  soil_moisture_7d_mean double precision,
  soil_moisture_14d_mean double precision,
  soil_moisture_30d_mean double precision,
  wind_speed_7d_mean double precision,
  wind_speed_14d_mean double precision,
  wind_speed_30d_mean double precision,
  pressure_7d_mean double precision,
  pressure_14d_mean double precision,
  pressure_30d_mean double precision,
  temperature_anomaly_c double precision,
  dewpoint_anomaly_c double precision,
  soil_moisture_anomaly double precision,
  pressure_anomaly_pa double precision,
  baseline_years smallint,
  spatial_grid_cells smallint NOT NULL,
  source_id text NOT NULL REFERENCES data_sources(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(location_id, date, source_id),
  CHECK (temperature_min_c IS NULL OR temperature_mean_c IS NULL OR temperature_min_c <= temperature_mean_c),
  CHECK (temperature_max_c IS NULL OR temperature_mean_c IS NULL OR temperature_max_c >= temperature_mean_c),
  CHECK (soil_moisture_mean IS NULL OR soil_moisture_mean BETWEEN 0 AND 1),
  CHECK (wind_speed_mean_ms IS NULL OR wind_speed_mean_ms >= 0),
  CHECK (surface_pressure_pa IS NULL OR surface_pressure_pa > 0),
  CHECK (spatial_grid_cells > 0)
);
CREATE INDEX IF NOT EXISTS weather_daily_date_idx ON weather_daily(date);
CREATE INDEX IF NOT EXISTS weather_daily_location_date_idx ON weather_daily(location_id, date DESC);

INSERT INTO data_sources(id, source_name, source_url, dataset_name, licence, configured, last_status, details)
VALUES (
  'ecmwf_user_netcdf_2020_2025',
  'ECMWF (as attributed in supplied files)',
  'user-provided local NetCDF; upstream URL not supplied',
  'Six-variable daily ECMWF NetCDF (exact product/version not stated)',
  NULL,
  false,
  'pending_processing',
  '{"source_period":"2020-01-01/2025-12-31","rainfall_available":false}'::jsonb
)
ON CONFLICT(id) DO NOTHING;
