-- Keep the project's operating scope limited to East Champaran, Muzaffarpur,
-- and Patna, including all active blocks/sub-districts within those districts.
CREATE OR REPLACE FUNCTION mausam_supported_district(location_key text)
RETURNS boolean LANGUAGE sql STABLE AS $$
  WITH RECURSIVE parents(id,parent_id,level,state_code,district_code) AS (
    SELECT id,parent_id,level,state_code,district_code
    FROM locations WHERE id=location_key AND active=true
    UNION ALL
    SELECT l.id,l.parent_id,l.level,l.state_code,l.district_code
    FROM locations l JOIN parents p ON l.id=p.parent_id
    WHERE l.active=true
  )
  SELECT EXISTS (
    SELECT 1 FROM parents
    WHERE level='district' AND state_code='10' AND district_code IN ('213','208','212')
  )
$$;
