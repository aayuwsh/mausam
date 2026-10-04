CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS data_sources (
  id text PRIMARY KEY, source_name text NOT NULL, source_url text NOT NULL,
  dataset_name text, licence text, configured boolean NOT NULL DEFAULT false,
  last_retrieved_at timestamptz, last_status text NOT NULL DEFAULT 'unavailable',
  details jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS locations (
  id text PRIMARY KEY, name text NOT NULL, level text NOT NULL CHECK(level IN ('state','district','block','subdistrict')),
  parent_id text REFERENCES locations(id), boundary extensions.geometry(MultiPolygon,4326),
  source_id text REFERENCES data_sources(id), active boolean NOT NULL DEFAULT true,
  state_code text, district_code text, block_code text, subdistrict_code text,
  source_code text, source_name text, source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(parent_id, name, level)
);
CREATE INDEX IF NOT EXISTS locations_boundary_gix ON locations USING GIST(boundary);
CREATE INDEX IF NOT EXISTS locations_parent_idx ON locations(parent_id, level);
CREATE INDEX IF NOT EXISTS locations_scope_code_idx ON locations(level, state_code, district_code, source_code);

CREATE TABLE IF NOT EXISTS farmer_profiles (
  id uuid PRIMARY KEY, auth_user_id uuid UNIQUE NOT NULL,
  display_name text NOT NULL, language text NOT NULL DEFAULT 'hi' CHECK(language IN ('hi','en')),
  location_id text NOT NULL REFERENCES locations(id), crop_id text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crops (
  id text PRIMARY KEY, name text NOT NULL, language text NOT NULL DEFAULT 'en', active boolean NOT NULL DEFAULT true
);
ALTER TABLE farmer_profiles DROP CONSTRAINT IF EXISTS farmer_profiles_crop_id_fkey;
ALTER TABLE farmer_profiles ADD CONSTRAINT farmer_profiles_crop_id_fkey FOREIGN KEY(crop_id) REFERENCES crops(id);

CREATE TABLE IF NOT EXISTS crop_calendar (
  id uuid PRIMARY KEY, crop_id text NOT NULL REFERENCES crops(id), season text NOT NULL,
  location_id text REFERENCES locations(id), sowing_start date, sowing_end date,
  transplanting_start date, transplanting_end date, harvest_start date, harvest_end date,
  water_requirement text, rainfall_preference text, flood_tolerance text, drought_tolerance text,
  source_id text NOT NULL REFERENCES data_sources(id), source_reference text NOT NULL,
  approved boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weather_observations (
  id bigserial PRIMARY KEY, location_id text NOT NULL REFERENCES locations(id), observed_at timestamptz NOT NULL,
  rainfall_mm double precision, temperature_c double precision, humidity_pct double precision,
  wind_ms double precision, pressure_hpa double precision, soil_moisture double precision,
  source_id text NOT NULL REFERENCES data_sources(id), retrieved_at timestamptz NOT NULL,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb, UNIQUE(location_id, observed_at, source_id)
);

CREATE TABLE IF NOT EXISTS climate_indices (
  id bigserial PRIMARY KEY, index_name text NOT NULL, valid_at date NOT NULL,
  value_1 double precision, value_2 double precision, phase integer, amplitude double precision,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_id text NOT NULL REFERENCES data_sources(id), retrieved_at timestamptz NOT NULL,
  UNIQUE(index_name, valid_at, source_id)
);
ALTER TABLE climate_indices ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS model_versions (
  version text PRIMARY KEY, algorithm text NOT NULL, training_start date, training_end date,
  features jsonb NOT NULL DEFAULT '[]'::jsonb, metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  artifact_uri text, status text NOT NULL DEFAULT 'unvalidated', created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS forecast_runs (
  id uuid PRIMARY KEY, model_version text REFERENCES model_versions(version), forecast_issue_at timestamptz,
  generated_at timestamptz NOT NULL DEFAULT now(), data_timestamp timestamptz,
  source_ids jsonb NOT NULL DEFAULT '[]'::jsonb, training_period jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'unvalidated', details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS provider_forecasts (
  id bigserial PRIMARY KEY, location_id text NOT NULL REFERENCES locations(id),
  provider text NOT NULL, model_name text NOT NULL, issue_at timestamptz,
  retrieved_at timestamptz NOT NULL, payload jsonb NOT NULL,
  UNIQUE(location_id, provider, model_name, retrieved_at)
);
CREATE INDEX IF NOT EXISTS provider_forecasts_freshness_idx ON provider_forecasts(location_id, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS predictions (
  id bigserial PRIMARY KEY, forecast_run_id uuid NOT NULL REFERENCES forecast_runs(id),
  location_id text NOT NULL REFERENCES locations(id), horizon_days integer NOT NULL CHECK(horizon_days IN (7,14,21,30)),
  expected_rainfall_mm double precision, rainfall_anomaly_mm double precision,
  rain_probability double precision, dry_spell_probability double precision,
  heavy_rain_probability double precision, onset_probability double precision,
  expected_dry_spell_days integer, daily_values jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(forecast_run_id, location_id, horizon_days)
);

CREATE TABLE IF NOT EXISTS advisory_rules (
  id uuid PRIMARY KEY, rule_key text UNIQUE NOT NULL, crop_id text REFERENCES crops(id),
  season text, condition jsonb NOT NULL, title_en text NOT NULL, body_en text NOT NULL,
  title_hi text, body_hi text, source_id text NOT NULL REFERENCES data_sources(id),
  approved boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS advisories (
  id uuid PRIMARY KEY, farmer_id uuid REFERENCES farmer_profiles(id), location_id text NOT NULL REFERENCES locations(id),
  prediction_id bigint REFERENCES predictions(id), rule_id uuid REFERENCES advisory_rules(id),
  kind text NOT NULL DEFAULT 'crop_advisory' CHECK(kind IN ('daily_weather','severe_weather','crop_advisory','sowing_advisory')),
  title text NOT NULL, body text NOT NULL, language text NOT NULL CHECK(language IN ('hi','en')),
  source_name text NOT NULL, source_url text NOT NULL, valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL, approved boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification_preferences (
  farmer_id uuid PRIMARY KEY REFERENCES farmer_profiles(id) ON DELETE CASCADE,
  daily_weather boolean NOT NULL DEFAULT false, severe_weather boolean NOT NULL DEFAULT true,
  crop_advisory boolean NOT NULL DEFAULT true, sowing_advisory boolean NOT NULL DEFAULT true,
  sms_enabled boolean NOT NULL DEFAULT true, whatsapp_enabled boolean NOT NULL DEFAULT false,
  browser_push_enabled boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notifications (
  id uuid PRIMARY KEY, farmer_id uuid NOT NULL REFERENCES farmer_profiles(id), advisory_id uuid REFERENCES advisories(id),
  channel text NOT NULL CHECK(channel IN ('sms','whatsapp','web_push')), provider text,
  status text NOT NULL DEFAULT 'queued', provider_message_id text, error_code text,
  created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id bigserial PRIMARY KEY, actor_id uuid, action text NOT NULL, entity_type text NOT NULL,
  entity_id text, created_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS admin_roles (
  auth_user_id uuid PRIMARY KEY, role text NOT NULL CHECK(role IN ('admin','expert','analyst')),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE farmer_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE crops ENABLE ROW LEVEL SECURITY;
ALTER TABLE crop_calendar ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE forecast_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE advisories ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_forecasts ENABLE ROW LEVEL SECURITY;
ALTER TABLE climate_indices ENABLE ROW LEVEL SECURITY;
ALTER TABLE weather_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE advisory_rules ENABLE ROW LEVEL SECURITY;

CREATE POLICY locations_read_active ON locations FOR SELECT TO anon, authenticated USING(active = true);
CREATE POLICY crops_read_active ON crops FOR SELECT TO anon, authenticated USING(active = true);
CREATE POLICY calendar_read_approved ON crop_calendar FOR SELECT TO anon, authenticated USING(approved = true);
CREATE POLICY sources_read ON data_sources FOR SELECT TO anon, authenticated USING(configured = true);
CREATE POLICY model_versions_read_validated ON model_versions FOR SELECT TO anon, authenticated USING(status = 'validated');
CREATE POLICY forecast_runs_read_validated ON forecast_runs FOR SELECT TO anon, authenticated USING(status = 'validated');
CREATE POLICY predictions_read_validated ON predictions FOR SELECT TO anon, authenticated USING(
  EXISTS (SELECT 1 FROM forecast_runs f WHERE f.id = forecast_run_id AND f.status = 'validated')
);
CREATE POLICY advisories_read_approved ON advisories FOR SELECT TO anon, authenticated USING(approved = true);
CREATE POLICY profile_read_self ON farmer_profiles FOR SELECT TO authenticated USING(auth_user_id = auth.uid());
CREATE POLICY profile_insert_self ON farmer_profiles FOR INSERT TO authenticated WITH CHECK(auth_user_id = auth.uid());
CREATE POLICY profile_update_self ON farmer_profiles FOR UPDATE TO authenticated USING(auth_user_id = auth.uid()) WITH CHECK(auth_user_id = auth.uid());
CREATE POLICY preferences_read_self ON notification_preferences FOR SELECT TO authenticated USING(
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
);
CREATE POLICY preferences_write_self ON notification_preferences FOR ALL TO authenticated USING(
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
) WITH CHECK(
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
);
