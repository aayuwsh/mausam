-- Source-code provenance fields used by the supplied LGD-coded MAUSAM extract.
-- Retire any older point-level rows; their geometry cannot be relabelled as sub-district data.
ALTER TABLE locations DROP CONSTRAINT IF EXISTS locations_level_check;
UPDATE locations SET active=false, level='legacy' WHERE level='panchayat';
ALTER TABLE locations ADD CONSTRAINT locations_level_check
  CHECK (level IN ('state','district','block','subdistrict','legacy'));
ALTER TABLE locations ADD COLUMN IF NOT EXISTS state_code text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS district_code text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS block_code text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS subdistrict_code text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS source_code text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS source_name text;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS locations_scope_code_idx ON locations(level, state_code, district_code, source_code);

-- Operationally expose only the project-approved district set.
CREATE OR REPLACE FUNCTION mausam_supported_district(location_key text)
RETURNS boolean LANGUAGE sql STABLE AS $$
  WITH RECURSIVE parents(id,parent_id,level) AS (
    SELECT id,parent_id,level FROM locations WHERE id=location_key AND active=true
    UNION ALL
    SELECT l.id,l.parent_id,l.level FROM locations l JOIN parents p ON l.id=p.parent_id WHERE l.active=true
  )
  SELECT EXISTS (SELECT 1 FROM parents WHERE level='district' AND id IN ('IN-BR-D-213','IN-BR-D-208','IN-BR-D-212'))
$$;
