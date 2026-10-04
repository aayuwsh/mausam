# MAUSAM

**Know Your Weather. Plan Your Crop.** A premium, farmer-first climate intelligence interface and API scaffold. The visual system follows the attached MAUSAM brief: warm paper-and-ochre palette, editorial type, numbered sections, responsive mobile-first account flow and explicit separation between raw source output and validated MAUSAM predictions.

## Project structure

- `src/` — React/Vite client. Public home, email/password registration and login, weather, map, advisory, farmer dashboard, alert preferences, model information and admin routes.
- `backend/app/` — FastAPI endpoints, provider contracts, Supabase session verification, scheduled weather and public climate-index ingestion plus notification jobs, and chronological validation helpers.
- `backend/migrations/001_initial_schema.sql`–`008_farm_journal.sql` — PostGIS schema and incremental updates, including the account-owned farm diary.
- `backend/app/prepare_mausam_geography.py` — decodes the supplied national TopoJSON files, filters actual geometry by Bihar state LGD code 10 and district LGD codes 213/208/212, and validates the resulting hierarchy.
- `backend/data/geography/mausam/bihar/` — filtered Bihar state, district, block and sub-district GeoJSON, plus an ordered `locations.geojson` import file. `public/data/mausam/bihar/` contains the same browser map layers.
- `backend/app/load_boundaries.py` — source-attributed GeoJSON loader with source-CRS transformation support.
- `backend/data/` — prepared Bihar district boundaries and user-provided ECMWF-attributed NetCDF files organized by 2020–2025.
- `docker-compose.yml` — local PostGIS database, API, Celery worker/beat and Redis; migrations initialize a new local database volume.
- `backend/app/inspect_weather_dataset.py` — metadata-only audit for the supplied NetCDF files (variables, times, units and GRIB semantics).

The `Maps` directory contained six national TopoJSON files: states (36), districts (785), blocks (7,146), sub-districts (6,471), parliament constituencies (543) and assembly constituencies (4,177). The source properties identify `name`, `lgd`, `census`, `state`, `state_lgd`, `district`, and `dist_lgd` as applicable. The filtered files retain all source properties and add normalized IDs, parent IDs, hierarchy level, and source-code fields. Exact filtered counts: Bihar 1, the requested districts 3, blocks 66 and sub-districts 66. Source spelling is `Purbi Champaran`; the UI label is mapped to `East Champaran` using LGD 213. Sub-district records carry `dist_lgd` but no block parent code, so farmer selection follows the supplied hierarchy `Bihar → district → sub-district`; block boundaries remain a separate map layer. Parliament/assembly geometries are not used. The original whole-India files remain untouched in the supplied Downloads folder.

The Maps files do not include a source/edition or licence statement, so the source catalogue must not be marked approved/configured for production until that provenance and reuse permission are established. The weather files are metadata-audited and processed locally; their exact ECMWF product/version, upstream URL and reuse terms are not stated in the files. No validated local prediction model or approved crop rules exist yet.

## Run the interface

```sh
npm install
npm run dev
```

For the frontend and local API together, use `npm run dev:full`. Stop both with `Ctrl+C` in that terminal. Keep using `npm run dev` when running the API separately.

## Run the API and background services

1. Install Docker Desktop, then start the dedicated local PostGIS database and services with `docker compose up --build -d`. A fresh database volume initializes migrations 001–008. The API image includes the processed administrative, crop-calendar, and dataset-status files; raw inputs are excluded from the build context. The local Compose API uses its local `db` service. The fallback password is for local development only; set `MAUSAM_DB_PASSWORD` in `.env` before using a shared machine.
2. For the existing Postgres/Supabase database, apply any migrations it is missing, including `008_farm_journal.sql`. Docker's `/docker-entrypoint-initdb.d` scripts only run when a database volume is first created; they do not migrate an existing volume. Apply remote migrations with the Supabase SQL editor or `psql` using the commands below.
3. Install backend requirements into a Python 3.12 environment, then run `uvicorn backend.app.main:app --reload --port 8000`.
4. Set `VITE_API_URL` and the public Supabase URL/anon key before starting/rebuilding the frontend. Set Supabase email confirmation and redirect URLs in the Supabase dashboard.
5. Once the Docker services are running, inspect the supplied weather metadata without importing values: `docker compose exec api python -m backend.app.inspect_weather_dataset backend/data --output backend/data/weather_audit.json`. Review the report and confirm source/reuse terms before building an observation importer. Compose mounts `backend/data`, so the report is saved to the project folder.
6. Rebuild the filtered geography when the supplied source files change: `python -m backend.app.prepare_mausam_geography "<Maps folder>" --output backend/data/geography/mausam/bihar`, then copy its GeoJSON outputs into `public/data/mausam/bihar/`. This repo already contains the extract generated from the supplied Maps folder.
7. After documenting and approving the Maps dataset provenance/licence in `data_sources`, load the ordered hierarchy with `python -m backend.app.load_boundaries backend/data/geography/mausam/bihar/locations.geojson --source-id <approved-source-id>`. Its coordinates are WGS84; no projected-coordinate transform is needed.

Never commit `.env`, send API credentials in chat, or expose server keys through `VITE_` variables. The only browser Supabase values are its public URL and anon key; server-side verification uses the user's bearer session.

### Database connection and profile sync

Supabase Auth configuration (`SUPABASE_URL` and `SUPABASE_ANON_KEY`) verifies login sessions; it does **not** connect the API to PostgreSQL. Normalized farmer profiles, notification preferences, and farm diary sync require `DATABASE_URL` in the project-root `.env`, set to the intended Postgres/Supabase URI. Apply migrations 001–008 to that same database before profile writes and diary sync. Keep its password server-side and restart the API after changing `.env`. Local API runs also accept `MAUSAM_DATABASE_URL`; Compose deliberately connects to its local PostGIS service. `GET /api/v1/health` is a liveness check; `GET /api/v1/ready` checks database readiness. If the optional database is unreachable, the API starts in degraded mode: local read-only data and weather routes can still run, while database-backed routes report unavailable.

## Routes

- `/` — premium landing, weather status, crop decision path, advisory, map preview, alerts, climate inputs and source register.
- `/register`, `/signin`, `/login` — separate farmer account flows using Supabase email confirmation, password sign-in and password reset when configured.
- `/dashboard` — signed-in farmer profile, available validated forecast horizons and approved advisories.
- `/weather` — real 2020–2025 processed sub-district observations, plus the optional current-location Open-Meteo point forecast (limited client-side to the three district polygons) and clearly separated validated-forecast status.
- `/map` — SVG rendering of actual district/block geometry from the filtered extract (or loaded PostGIS polygons), with district filtering, district/block/sub-district level selection and selected-area highlighting.
- `/advisory`, `/notifications`, `/model`, `/admin` — sourced farmer advisories, opt-in preferences, validated model metadata and role-protected system/source status.
- The floating `Ask Mausam` assistant is available on the landing page and farmer workspace. It accepts typed questions and browser speech recognition in English or Hindi, and can read answers aloud using the backend Gemini speech service. The backend fetches selected-area Open-Meteo weather and crop-calendar context before answering. Configure server-only `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) in the project-root `.env`, then restart the API; without the key, the assistant reports that setup is missing and does not fabricate an answer. Speech recognition availability and voice quality depend on the browser/device; the microphone is activated only after the user taps it.
- The farmer dashboard includes a forecast-based daily action card, an account-backed farm diary with device-local offline notes and manual sync, an estimate calculator using a farmer-entered market price, opt-in browser reminders, and AI soil-report/photo explainers. Soil/photo assistance requires a signed-in farmer and the server-side Gemini key; photo checks describe possibilities, not diagnoses. Market price data is not automatically fetched: the calculator labels farmer-entered prices and links to e-NAM's public price dashboard.

## What is ready and what still needs external inputs

- **Ready in code:** responsive premium design, English/Hindi UI, Supabase email confirmation, password sign-in and password reset, profile save/load, API auth verification, source-attributed location selection, actual geometry rendering, raw EC46 retrieval, fresh validated-prediction reads, approval-gated advisory reads, alert preferences, role-protected admin metrics/source catalogue, async job scaffolding and time-ordered validation metrics, and an opt-in NOAA/BOM climate-index importer.
- **Not live without setup/data:** database import and verified database location/crop lists, trained/validated model and predictions, crop recommendations/advisory content, and sending SMS/WhatsApp/browser push. The processed historical archive already serves from local files through the API. The current-location point forecast requires browser location permission and internet access; it is clearly identified as provider output, not a validated MAUSAM forecast.
- **Accuracy boundary:** Open-Meteo EC46 is presented as raw provider data only. It does not become a validated subdistrict forecast or an agricultural recommendation by being displayed. The dashboard/API only surface a MAUSAM prediction after a validated run is stored and fresh.

### Historical model validation

The current rainfall model is a research candidate, not an operational forecast. `backend/models/national-rainfall-v1/model_report.json` contains a chronological held-out backtest (train issue dates before 2024; assess 2024–2025), plus a recent-7-day-rate baseline and a past-only location/month climatology baseline. Re-run with `python -m backend.app.train_rainfall_model`. The latest supplied IMD rainfall ends 2025-12-31, and no archived Open-Meteo forecast issue/valid-time pairs were supplied, so this can assess the custom historical model against IMD outcomes but cannot validate current provider forecasts. The report marks the model non-operational until newer observations and a provider backtest are available; archived predictions are not current weather.

See [CONNECTIONS.md](CONNECTIONS.md) for the account, dataset and credential checklist.

## Historical weather processing

The supplied weather directory has been inspected; its actual structure, observed units, date gaps, processing choices and limitations are recorded in [backend/data/weather_dataset.md](backend/data/weather_dataset.md). The source has one daily sample at 10:00 UTC and no precipitation, so true daily extrema and rainfall are not derived. The pipeline reads one monthly NetCDF file at a time and applies polygon-cell intersection weights to the 66 supplied subdistricts in East Champaran, Muzaffarpur and Patna.

Install the backend requirements and create database-ready local output:

```sh
python -m pip install -r backend/requirements.txt
python -m backend.app.process_weather_dataset \
  --input backend/data \
  --geography backend/data/geography/mausam/bihar \
  --output backend/data/processed/weather_daily.csv
```

This writes the daily CSV, a `.manifest.json`, and a small `.index.json` used by the API to seek directly to one subdistrict's rows. The Weather page reads this local archive immediately; it does not need a database. The original NetCDF inputs remain unchanged. Run the tests with:

```sh
python -m unittest discover -s backend/tests -v
```

For an existing local Compose volume, apply migration 003 with:

```sh
docker compose exec -T db psql -U mausam -d mausam -v ON_ERROR_STOP=1 \
  -f /docker-entrypoint-initdb.d/003_weather_daily.sql
```

For an existing Postgres/Supabase database, run:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/003_weather_daily.sql
```

Apply the new three-district database scope guard to an existing database with:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/007_limit_supported_districts.sql
```

Apply the farm diary schema to an existing database with:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/008_farm_journal.sql
```

For a fresh local Compose database, migration 003 is mounted for first initialization. After migration 003 and the verified location rows are in place, import with:

```sh
python -m backend.app.process_weather_dataset \
  --input backend/data \
  --geography backend/data/geography/mausam/bihar \
  --output backend/data/processed/weather_daily.csv \
  --database-url "$DATABASE_URL"
```

Upserts are batched in groups of 1,000 and conflict on `(location_id, date, source_id)`. The admin data-status card reads its status, dates and record counts from `weather_daily`; it remains “not imported” until rows are present. Keep database credentials in environment configuration and out of command history where possible.

## District/block live weather and crop calendar

The Weather page now selects one of the three supported Bihar districts and a supplied block. It requests current conditions and up to 16 days from Open-Meteo and shows a ten-day block-specific view. The user-supplied crop calendar CSVs are retained under `data/raw/agriculture/`, with a merged reference at `data/agriculture/crop_calendar_supplied.csv`. See [the integration notes](docs/WEATHER_CROP_ADVISORY.md) for provenance, current model limitations, and run commands.
