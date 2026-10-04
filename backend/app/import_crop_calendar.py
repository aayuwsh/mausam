"""Idempotently load extracted crop-calendar records as unapproved references."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import os
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data/agriculture/crop_calendar.csv"
SOURCE_ID = "bihar_district_contingency_plans_2013"
SOURCE_NAME = "Agriculture Contingency Plans (2013; source organization not stated in the supplied PDFs)"
DISTRICT_CODES = {"east champaran": "213", "purbi champaran": "213", "muzaffarpur": "208", "patna": "212"}
SOURCE_UPSERT_SQL = """INSERT INTO data_sources(id,source_name,source_url,dataset_name,licence,configured,last_status,details)
    VALUES($1,$2,NULL,$3,NULL,false,'review_required',$4::jsonb)
    ON CONFLICT(id) DO UPDATE SET source_name=EXCLUDED.source_name,dataset_name=EXCLUDED.dataset_name,
      details=EXCLUDED.details"""
CALENDAR_UPSERT_SQL = """INSERT INTO crop_calendar(
    id,crop_id,season,location_id,sowing_start,sowing_end,harvest_start,harvest_end,
    source_id,source_reference,approved,condition,sowing_window_start,sowing_window_end,
    harvest_window_start,harvest_window_end,duration_days,source_document,source_page,source_section,source_text)
  VALUES($1,$2,$3,$4,NULL,NULL,NULL,NULL,$5,$6,false,$7,$8,$9,NULL,NULL,$10,$11,$12,$13,$14)
  ON CONFLICT(source_id,source_reference) DO UPDATE SET crop_id=EXCLUDED.crop_id,season=EXCLUDED.season,
    location_id=EXCLUDED.location_id,condition=EXCLUDED.condition,sowing_window_start=EXCLUDED.sowing_window_start,
    sowing_window_end=EXCLUDED.sowing_window_end,source_document=EXCLUDED.source_document,
    source_page=EXCLUDED.source_page,source_section=EXCLUDED.source_section,source_text=EXCLUDED.source_text,
    approved=CASE WHEN crop_calendar.source_text IS DISTINCT FROM EXCLUDED.source_text
                    OR crop_calendar.condition IS DISTINCT FROM EXCLUDED.condition
                    OR crop_calendar.sowing_window_start IS DISTINCT FROM EXCLUDED.sowing_window_start
                    OR crop_calendar.sowing_window_end IS DISTINCT FROM EXCLUDED.sowing_window_end
                  THEN false ELSE crop_calendar.approved END"""


def crop_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def source_reference(row: dict[str, str]) -> str:
    text_hash = hashlib.sha256(row["source_text"].encode("utf-8")).hexdigest()[:12]
    return f"{row['source_document']}#page={row['source_page']}#{row['season']}#{crop_id(row['crop'])}#{text_hash}"


async def import_rows(database_url: str, csv_path: Path = CSV_PATH) -> int:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig", newline="")))
    if not rows:
        return 0
    from .db_connection import connect_database
    conn = await connect_database(database_url)
    imported = 0
    try:
        async with conn.transaction():
            await conn.execute(
                SOURCE_UPSERT_SQL,
                SOURCE_ID, SOURCE_NAME, "District crop sowing windows (2013)",
                '{"documents":["BR16_East Champaran_28.12.2013.pdf","BR26_Muzaffarpur_28.12.2013.pdf","BR27_Patna_28.12.2013.pdf"],"licence":"not stated in supplied documents","approval_required":true}',
            )
            for row in rows:
                district_code = DISTRICT_CODES.get(row["district"].strip().lower())
                if not district_code:
                    raise ValueError(f"Crop calendar district is outside the supported Bihar set: {row['district']}")
                district = await conn.fetchrow(
                    "SELECT id FROM locations WHERE level='district' AND state_code='10' AND district_code=$1 AND active=true",
                    district_code,
                )
                if not district:
                    raise ValueError(f"District is not loaded in locations: {row['district']}")
                cid = crop_id(row["crop"])
                await conn.execute("INSERT INTO crops(id,name,language,active) VALUES($1,$2,'en',true) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,active=true", cid, row["crop"])
                ref = source_reference(row)
                record_id = uuid5(NAMESPACE_URL, f"mausam:{SOURCE_ID}:{ref}:{row['season']}:{row['crop']}:{row['source_text']}")
                duration = int(row["duration_days"]) if row["duration_days"].isdigit() else None
                page = int(row["source_page"])
                await conn.execute(
                    CALENDAR_UPSERT_SQL,
                    record_id, cid, row["season"], district["id"], SOURCE_ID, ref, row["condition"],
                    row["sowing_start"] or None, row["sowing_end"] or None, duration,
                    row["source_document"], page, row["source_section"], row["source_text"],
                )
                imported += 1
    finally:
        await conn.close()
    return imported


def main():
    from .config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.database_url or os.environ.get("MAUSAM_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("Set MAUSAM_DATABASE_URL or DATABASE_URL after applying migrations 001-004.")
    print(f"Imported or refreshed {asyncio.run(import_rows(args.database_url, args.csv))} unapproved crop-calendar rows.")


if __name__ == "__main__":
    main()
