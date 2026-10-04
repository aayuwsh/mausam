"""Import supplied nationwide LGD GeoJSON boundaries into existing PostGIS locations."""
from __future__ import annotations
import asyncio, gzip, json
from pathlib import Path
from typing import Any
from .config import settings
from .db_connection import connect_database

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "data/processed/administrative"
SUPPORTED_STATE_CODE = "10"  # Bihar
SUPPORTED_DISTRICT_CODES = {"208", "212", "213"}  # Muzaffarpur, Patna, Purbi Champaran

async def main() -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured. Apply migrations 001–006 to the existing Supabase database first.")
    index=json.loads((ADMIN/"locations.json").read_text(encoding="utf-8"))
    # Keep operational locations aligned with the three districts the app serves.
    # This deliberately avoids activating every location in the nationwide LGD file.
    locations=[r for r in index.get("locations",[]) if
        (r.get("level")=="state" and str(r.get("state_code"))==SUPPORTED_STATE_CODE) or
        (str(r.get("state_code"))==SUPPORTED_STATE_CODE and str(r.get("district_code")) in SUPPORTED_DISTRICT_CODES)]
    states={}
    for row in locations:
        if row.get("state_code"):
            states[row["state_code"]]=row.get("state_name") or row["state_code"]
    con=await connect_database(settings.database_url)
    try:
        async with con.transaction():
            for code,name in states.items():
                await con.execute("""INSERT INTO locations(id,name,level,active,state_code,source_code,source_name)
                  VALUES($1,$2,'state',true,$3,$3,'user-supplied LGD Maps TopoJSON')
                  ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,active=true,state_code=EXCLUDED.state_code""",
                  f"IN-LGD-ST-{code}",name,code)
            by_id={r["id"]:r for r in locations}
            for level in ("district","block","subdistrict"):
                path=ADMIN/f"{level}s.geojson.gz"
                with gzip.open(path,"rt",encoding="utf-8") as f: collection=json.load(f)
                batch=[]
                for feature in collection.get("features",[]):
                    p=feature.get("properties",{}); item=by_id.get(p.get("id"))
                    if not item: continue
                    batch.append((item["id"],item["name"],level,item.get("parent_id"),json.dumps(feature["geometry"]),
                      item.get("state_code"),item.get("district_code"),item.get("block_code"),item.get("subdistrict_code"),
                      item.get("source_code"),"user-supplied LGD Maps TopoJSON",json.dumps(p)))
                if batch:
                    await con.executemany("""INSERT INTO locations(id,name,level,parent_id,boundary,state_code,district_code,block_code,subdistrict_code,source_code,source_name,source_attributes,active)
                      VALUES($1,$2,$3,$4,extensions.ST_Multi(extensions.ST_SetSRID(extensions.ST_GeomFromGeoJSON($5),4326)),$6,$7,$8,$9,$10,$11,$12::jsonb,true)
                      ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,level=EXCLUDED.level,parent_id=EXCLUDED.parent_id,boundary=EXCLUDED.boundary,
                        state_code=EXCLUDED.state_code,district_code=EXCLUDED.district_code,block_code=EXCLUDED.block_code,subdistrict_code=EXCLUDED.subdistrict_code,
                        source_code=EXCLUDED.source_code,source_name=EXCLUDED.source_name,source_attributes=EXCLUDED.source_attributes,active=true""",batch)
                print(f"Imported {len(batch)} {level} geometries")
        print(f"Imported nationwide hierarchy for {len(states)} states and {len(locations)} administrative units")
    finally:
        await con.close()

if __name__=="__main__": asyncio.run(main())
