# MAUSAM crop calendar source data

`crop_calendar_raw.csv` and `crop_calendar.csv` contain the normal sowing-window table from section 1.12 of the supplied 2013 Agriculture Contingency Plans for East Champaran, Muzaffarpur, and Patna. Original source wording, PDF filename, page, crop column, and extracted row text are retained in the raw file. The cleaned file retains the required ten core fields and page/document traceability.

The documents do not identify an issuing organization or usage licence in their supplied pages, so MAUSAM does not attribute them to ICAR-CRIDA or mark the source as configured. Source `condition` preserves the table's `Normal sowing period` and its Rainfed/Irrigated row. `sowing_start` and `sowing_end` are recurring month/week strings; no year or artificial day precision is added. The section does not give crop-level harvest windows or duration, so those fields are blank. A repeated East Champaran Maize/Kharif/Rainfed key contains two different source columns/windows; both are retained and flagged instead of collapsed.

The local calendar endpoint presents these as reference-only source records. Database import writes `approved=false`; an authorized agronomy review and source/licence verification are required before the source is configured or records can be used for approved crop selection. Calendar phase is descriptive calendar context. It does not itself generate a weather advisory.

## Regenerate

From the project root, with the three original PDFs in `~/Downloads/Chrome P-2` and the backend requirements installed:

```sh
python3 -m backend.app.extract_crop_calendar
```

Or specify a source folder and output folder:

```sh
python3 -m backend.app.extract_crop_calendar --pdf-dir /path/to/pdfs --output-dir data/agriculture
```

## Database setup/import

Apply existing migrations `001` through `003`, then `backend/migrations/004_crop_calendar_sources.sql`. Load the repository's supplied district/subdistrict locations first. After configuring `MAUSAM_DATABASE_URL` (or `DATABASE_URL`) for the target Postgres database:

```sh
python3 -m backend.app.import_crop_calendar
```

This import is repeatable and writes unapproved records. It does not bypass the source/licence or agronomy review gate. The three supplied PDFs are required locally for regeneration and are not copied into this repository.
