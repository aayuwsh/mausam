CREATE TABLE IF NOT EXISTS farm_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farmer_id uuid NOT NULL REFERENCES farmer_profiles(id) ON DELETE CASCADE,
  client_id uuid,
  record_type text NOT NULL CHECK (record_type IN ('activity','expense','soil_test','reminder')),
  recorded_on date NOT NULL DEFAULT CURRENT_DATE,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
  details text NOT NULL DEFAULT '' CHECK (length(details) <= 2000),
  quantity numeric(12,3),
  unit text CHECK (unit IS NULL OR length(unit) <= 40),
  amount numeric(12,2),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS farm_records_farmer_date_idx ON farm_records(farmer_id, recorded_on DESC, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS farm_records_client_id_idx ON farm_records(farmer_id, client_id) WHERE client_id IS NOT NULL;
ALTER TABLE farm_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS farm_records_read_self ON farm_records;
CREATE POLICY farm_records_read_self ON farm_records FOR SELECT TO authenticated USING (
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
);
DROP POLICY IF EXISTS farm_records_write_self ON farm_records;
CREATE POLICY farm_records_write_self ON farm_records FOR ALL TO authenticated USING (
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
) WITH CHECK (
  EXISTS (SELECT 1 FROM farmer_profiles p WHERE p.id = farmer_id AND p.auth_user_id = auth.uid())
);
