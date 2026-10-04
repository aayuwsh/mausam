"""Extract traceable sowing-window rows from the supplied 2013 district plans.

Requires pdfplumber (optional data-preparation dependency). The PDFs remain the
source of truth; this script never fills unsupported harvest or duration data.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data/agriculture"
DEFAULT_PDFS = {
    "BR16_East Champaran_28.12.2013.pdf": ("East Champaran", ["Maize", "Rice", "Wheat", "Pulses", "Maize"]),
    "BR26_Muzaffarpur_28.12.2013.pdf": ("Muzaffarpur", ["Rice", "Pigeonpea", "Wheat", "Maize", "Lentil"]),
    "BR27_Patna_28.12.2013.pdf": ("Patna", ["Pigeonpea", "Maize", "Rice", "Maize", "Chickpea", "Lentil", "Wheat", "Mustard", "Potato", "Pea"]),
}
CORE = ["district", "crop", "season", "condition", "sowing_start", "sowing_end", "harvest_start", "harvest_end", "duration_days", "source"]
EXTRA = ["source_document", "source_page", "source_section", "source_text", "source_column", "validation_status"]
MONTHS = {m.lower(): m for m in ("January February March April May June July August September October November December").split()}
MONTHS.update({"jan": "January", "feb": "February", "mar": "March", "apr": "April", "jun": "June", "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October", "nov": "November", "dec": "December"})
ORDINAL = {"1st": "week 1", "2nd": "week 2", "3rd": "week 3", "4th": "week 4", "last": "last week"}
MONTH_PATTERN = r"January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"


def parse_window(text: str) -> tuple[str, str]:
    """Normalize recurring source windows to month/week text without adding a year or day."""
    value = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip(" -")
    value = re.sub(r"\s*-\s*", " - ", value)
    if not value or value == "-":
        return "", ""

    def endpoint(part: str) -> str:
        part = part.strip()
        exact = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})\b", part, re.I)
        if exact:
            return f"{MONTHS[exact.group(2).lower()]} {int(exact.group(1))}"
        week = re.search(rf"\b(1st|2nd|3rd|4th|last)\s+week\s+of\s+({MONTH_PATTERN})\b", part, re.I)
        if week:
            return f"{MONTHS[week.group(2).lower()]}, {ORDINAL[week.group(1).lower()]}"
        month = re.search(rf"\b({MONTH_PATTERN})\b", part, re.I)
        return MONTHS[month.group(1).lower()] if month else part

    # Week/date ranges are split at the source dash; a month-only range such as June-July follows the same path.
    parts = value.split(" - ", 1)
    if len(parts) == 1:
        # Source forms occasionally use whitespace-separated month ranges.
        months = re.findall(rf"\b({MONTH_PATTERN})\b", value, re.I)
        if len(months) > 1:
            return MONTHS[months[0].lower()], MONTHS[months[-1].lower()]
        point = endpoint(value)
        return point, point
    return endpoint(parts[0]), endpoint(parts[1])


def _table_for_page(page):
    for table in page.extract_tables():
        if any("Sowing window" in str(cell) for row in table for cell in row):
            return table
    return None


def extract(pdf_paths: list[Path]) -> list[dict[str, str]]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Install backend/requirements.txt to extract source PDF tables (pdfplumber is required).") from exc
    rows = []
    for pdf_path in pdf_paths:
        if pdf_path.name not in DEFAULT_PDFS:
            raise ValueError(f"Unsupported source PDF: {pdf_path.name}")
        district, crop_columns = DEFAULT_PDFS[pdf_path.name]
        with pdfplumber.open(pdf_path) as pdf:
            found = False
            for page_number, page in enumerate(pdf.pages, 1):
                if "1.12 Sowing window" not in (page.extract_text() or ""):
                    continue
                table = _table_for_page(page)
                if table is None:
                    continue
                found = True
                header = " ".join(str(cell or "") for cell in table[0])
                # Patna's Rabi crop labels and rows continue onto the following page.
                if district == "Patna":
                    kharif_rows = table[1:3]
                    for raw_row in kharif_rows:
                        season, condition = _season_condition(raw_row[1] or "")
                        for index, crop in enumerate(crop_columns[:3], 2):
                            rows.append(_record(district, crop, season, condition, raw_row[index], pdf_path, page_number, "1.12 Sowing window for major field crops", f"{raw_row[1]} | {crop}: {raw_row[index] or '-'}", f"column_{index-1}"))
                    rabi_labels = [str(c or "").strip() for c in table[-1][2:]]
                    if len(rabi_labels) != 7:
                        raise ValueError(f"Could not read Patna's continued crop labels: {rabi_labels}")
                    # The following page contains the two Rabi rows in the same column order.
                    for next_number, next_page in enumerate(pdf.pages[page_number:], page_number + 1):
                        if "Rabi- Rainfed" not in (next_page.extract_text() or ""):
                            continue
                        rabi_table = next((t for t in next_page.extract_tables() if any("Rabi- Rainfed" in str(c) for r in t for c in r)), None)
                        if not rabi_table:
                            break
                        for raw_row in rabi_table:
                            label = str(raw_row[1] or "")
                            if not label.startswith("Rabi-"):
                                continue
                            season, condition = _season_condition(label)
                            for crop, cell, index in zip(rabi_labels, raw_row[2:9], range(1, 8)):
                                rows.append(_record(district, crop, season, condition, cell, pdf_path, next_number, "1.12 Sowing window for major field crops", f"{label} | {crop}: {cell or '-'}", f"column_{index}"))
                        break
                    continue

                # East Champaran and Muzaffarpur tables have one crop header row followed by season rows.
                crop_names = crop_columns
                for raw_row in table[1:]:
                    label = str(raw_row[1] or "").strip()
                    if not label or label == "-" or label.lower().startswith("what is"):
                        continue
                    if label.lower().startswith(("kharif", "rabi", "summer", "zaid")):
                        season, condition = _season_condition(label)
                        for crop, cell, index in zip(crop_names, raw_row[2:2+len(crop_names)], range(1, len(crop_names)+1)):
                            rows.append(_record(district, crop, season, condition, cell, pdf_path, page_number, "1.12 Sowing window for major field crops (normal sowing period)", f"{label} | {crop}: {cell or '-'}", f"column_{index}"))
            if not found:
                raise ValueError(f"No section 1.12 sowing-window table was found in {pdf_path}")
    return [row for row in rows if row["sowing_start"] or row["sowing_end"]]


def _season_condition(label: str) -> tuple[str, str]:
    parts = re.split(r"[-–]", label, maxsplit=1)
    season = parts[0].strip().title()
    qualifier = parts[1].strip() if len(parts) > 1 else ""
    return season, "; ".join(x for x in ("Normal sowing period", qualifier.title()) if x)


def _record(district, crop, season, condition, cell, pdf_path, page, section, source_text, column):
    text = str(cell or "").strip().replace("\n", " ")
    start, end = parse_window(text)
    return {"district": district, "crop": crop, "season": season, "condition": condition,
            "sowing_start": start, "sowing_end": end, "harvest_start": "", "harvest_end": "", "duration_days": "",
            "source": f"Agriculture Contingency Plan for District: {district} (2013)",
            "source_document": pdf_path.name, "source_page": str(page), "source_section": section,
            "source_text": source_text, "source_column": column, "validation_status": "source_checked" if start else "missing_sowing_window"}


def validate(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict]:
    counts = Counter((r["district"], r["crop"], r["season"], r["condition"]) for r in rows)
    for row in rows:
        key = (row["district"], row["crop"], row["season"], row["condition"])
        if counts[key] > 1:
            row["validation_status"] = "flagged_duplicate_key_keep_all_source_windows"
    report = {"records": len(rows), "districts": sorted({r["district"] for r in rows}),
              "crops": sorted({r["crop"] for r in rows}),
              "duplicate_keys": [list(k) for k, n in counts.items() if n > 1],
              "records_missing_harvest_window": sum(not r["harvest_start"] for r in rows),
              "records_missing_duration": sum(not r["duration_days"] for r in rows),
              "method": "Text/table extraction with pdfplumber; all window values retain source month/week precision; no year or unsupported harvest/duration values are added."}
    return rows, report


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=Path.home() / "Downloads/Chrome P-2")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    pdf_paths = [args.pdf_dir / name for name in DEFAULT_PDFS]
    missing = [str(path) for path in pdf_paths if not path.is_file()]
    if missing:
        raise SystemExit("Missing source PDFs:\n" + "\n".join(missing))
    rows, report = validate(extract(pdf_paths))
    write_csv(args.output_dir / "crop_calendar_raw.csv", rows, CORE + EXTRA)
    write_csv(args.output_dir / "crop_calendar.csv", rows, CORE + [c for c in EXTRA if c != "source_column"])
    (args.output_dir / "crop_calendar_validation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
