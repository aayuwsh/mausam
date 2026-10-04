"""Import a source-attributed GeoJSON FeatureCollection into PostGIS.

Each feature must have normalized properties: id, name, level and parent_id.
Pass --source-srid to transform projected source coordinates to WGS84.
Every district/block/subdistrict must be traceable to the source catalogue recorded first.
"""
import argparse
import asyncio
import json
from pathlib import Path

from .config import settings
from .db_connection import connect_database

async def load(path: Path, source_id: str, source_srid: int = 4326):
    if not settings.database_url:
        raise RuntimeError("Set DATABASE_URL before loading boundaries.")
    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection.get("type") != "FeatureCollection":
        raise ValueError("Expected GeoJSON FeatureCollection.")
    features = collection.get("features", [])
    district_count = sum(f.get("properties", {}).get("level") == "district" for f in features)
    if not district_count:
        raise ValueError("Boundary file must contain at least one district feature.")
    conn = await connect_database(settings.database_url)
    try:
        async with conn.transaction():
            source = await conn.fetchval("SELECT id FROM data_sources WHERE id=$1 AND configured=true", source_id)
            if not source:
                raise ValueError("Record and approve the dataset source/licence in data_sources before import.")
            for feature in features:
                p = feature.get("properties") or {}
                if p.get("level") not in {"state", "district", "block", "subdistrict"} or not all(p.get(k) for k in ("id", "name")):
                    raise ValueError("Each feature needs id, name and a supported administrative level.")
                geometry = feature.get("geometry")
                if not geometry:
                    raise ValueError(f"Boundary geometry missing for {p.get('name')}.")
                await conn.execute(
                    """INSERT INTO locations(id,name,level,parent_id,boundary,source_id,active,
                           state_code,district_code,block_code,subdistrict_code,source_code,source_name,source_attributes)
                       VALUES($1,$2,$3,$4,extensions.ST_Multi(extensions.ST_Transform(
                         extensions.ST_SetSRID(extensions.ST_GeomFromGeoJSON($5),$7),4326)),$6,true,
                         $8,$9,$10,$11,$12,$13,$14::jsonb)
                       ON CONFLICT(id) DO UPDATE SET name=$2,level=$3,parent_id=$4,boundary=EXCLUDED.boundary,
                         source_id=$6,active=true,state_code=$8,district_code=$9,block_code=$10,
                         subdistrict_code=$11,source_code=$12,source_name=$13,source_attributes=$14::jsonb""",
                    str(p["id"]), str(p["name"]), str(p["level"]), p.get("parent_id"), json.dumps(geometry), source_id, source_srid,
                    str(p.get("state_code") or "10"), p.get("district_code"), p.get("block_code"), p.get("subdistrict_code"),
                    str(p.get("source_code") or ""), p.get("source_name"), json.dumps(p),
                )
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-srid", type=int, default=4326, help="EPSG SRID of input coordinates (default: 4326)")
    args = parser.parse_args()
    asyncio.run(load(args.geojson, args.source_id, args.source_srid))
