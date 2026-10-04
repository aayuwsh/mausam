-- Keep the existing crop_calendar model and extend it for recurring, sourced windows.
ALTER TABLE data_sources ALTER COLUMN source_url DROP NOT NULL;
ALTER TABLE crop_calendar
  ADD COLUMN IF NOT EXISTS condition text,
  ADD COLUMN IF NOT EXISTS sowing_window_start text,
  ADD COLUMN IF NOT EXISTS sowing_window_end text,
  ADD COLUMN IF NOT EXISTS harvest_window_start text,
  ADD COLUMN IF NOT EXISTS harvest_window_end text,
  ADD COLUMN IF NOT EXISTS duration_days integer,
  ADD COLUMN IF NOT EXISTS duration_min_days integer,
  ADD COLUMN IF NOT EXISTS duration_max_days integer,
  ADD COLUMN IF NOT EXISTS source_document text,
  ADD COLUMN IF NOT EXISTS source_page integer,
  ADD COLUMN IF NOT EXISTS source_section text,
  ADD COLUMN IF NOT EXISTS source_text text;

CREATE UNIQUE INDEX IF NOT EXISTS crop_calendar_source_reference_uq
  ON crop_calendar(source_id, source_reference);

CREATE INDEX IF NOT EXISTS crop_calendar_location_crop_idx
  ON crop_calendar(location_id, crop_id, season, approved);

-- Existing data is untouched. New extracted plans are loaded as unapproved until reviewed.
