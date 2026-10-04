# Connecting live services to MAUSAM

The UI and service contracts are wired. The supplied NetCDF files have been audited and processed into a real 2020–2025 local sub-district archive. The Weather page reads this archive through the local API immediately. Postgres/Supabase import still requires a direct `DATABASE_URL` and loaded verified locations. Exact ECMWF product/version and reuse terms remain unstated in the files. MAUSAM continues to leave rainfall predictions and advice unavailable until their own real sources and validation are in place. Keep credentials in local `.env` or the hosting provider's secret manager; do not paste secrets into chat or commit them.

## Deployment pieces

```text
React/Vite farmer & admin UI
  ├── public Supabase URL + anon key → Supabase email/password session
  └── authenticated API calls → FastAPI → separate Postgres/PostGIS
                                      ├── scheduled Celery worker + Redis
                                      ├── weather/climate source clients
                                      ├── approved crop/advisory catalogue
                                      └── configured SMS / optional WhatsApp
```

## Required setup

1. **Supabase email authentication:** Use the configured Mausam project. Enable email confirmation and password sign-in; set the site URL and redirect allow-list for local and deployed routes. Put the project URL and public anon key in the browser environment and backend environment. New users confirm by email, existing users sign in with email and password, and password recovery uses Supabase email links. The API validates bearer tokens before profile, preference or admin endpoints.
2. **Postgres + PostGIS:** For local development, install Docker Desktop and run `docker compose up --build -d`. Compose starts a dedicated PostGIS database and applies migrations 001–003 on first database-volume initialization. Migration 003 creates the daily weather table. The fallback password is local-only; set `MAUSAM_DB_PASSWORD` in `.env` on shared machines. For an external database, set `MAUSAM_DATABASE_URL` and apply each unapplied migration yourself. Do not reuse the land-records project's database. Create an explicit admin role row for authorized staff; ordinary users receive no admin metrics.
3. **Administrative geometry:** The current MAUSAM geography is the supplied Maps TopoJSON extract at `backend/data/geography/mausam/bihar/`. It contains Bihar plus East Champaran, Muzaffarpur and Patna, with their blocks and sub-districts. Sub-district records link directly to districts through `dist_lgd`; they do not identify a parent block. Review source edition/licence, apply migrations 001 and 002, then load `locations.geojson` with the source record ID after that source is approved. The weather/farmer location flow is district → sub-district; blocks remain a separate map layer.
4. **Forecast and observations:** `backend/data/2020` through `backend/data/2025` contain 72 supplied NetCDF files with ECMWF GRIB metadata. Dataset findings are in `backend/data/weather_dataset.md`; the daily processing and optional batch import command are in the README’s “Historical weather processing” section. The local CSV archive is already generated. To populate Postgres/Supabase, apply migration 003, load the verified sub-district rows, and run the processor with `--database-url "$DATABASE_URL"`. The raw files attribute data to ECMWF but do not name the exact product/version or upstream URL; confirm reuse terms before commercial use. One instantaneous source value per day does not provide true daily temperature/wind extrema, and there is no precipitation field. Do not treat historical weather as a current forecast or derive operational rainfall predictions directly from it. Open-Meteo EC46 retrieval is a separate raw 46-day ensemble provider path, not the MAUSAM ML model, not subdistrict-scale validation, and not itself advice.
5. **Climate indices:** An opt-in scheduled importer reads NOAA CPC ONI, NOAA PSL DMI (HadISST), and BOM RMM daily files, storing published values with source URLs and retrieval times. Set `ENABLE_PUBLIC_CLIMATE_INGESTION=true` after source/policy review. ONI remains a global climate index, not a local monsoon prediction.
6. **Crop calendars and advisories:** Source an authoritative Bihar/ICAR calendar and district/season-specific recommendations. Record source, publication/version, applicable geography and approval in `data_sources`, `crop_calendar`, `advisory_rules`. Entries are not visible/selectable until approved and active. The system does not generate unsupported agricultural instructions.
7. **Alert channels:** Supabase email confirmation handles new account verification. For later outbound alerts, select/configure Twilio or adapt the `NotificationProvider` for the chosen India SMS provider; configure templates/sender requirements. Set `ENABLE_NOTIFICATION_DISPATCH=true` only after approved advisories, opt-in preferences, credentials, rate limits and a non-production delivery check are ready. WhatsApp Business credentials and browser push service-worker/VAPID setup are additional work; preference flags alone do not send.
8. **IMD station observations:** IMD API documentation lists AWS station endpoints, but says the consumer must provide a public IP for whitelisting. Contact IMD/provider support to request authorized access and provide your deployment egress IP directly to them; do not send it in chat. The existing UI does not claim this feed is connected.
9. **Model validation:** Provide quality-controlled, time-indexed weather observations and outcomes. Train/evaluate using chronological folds only; store Brier, MAE, RMSE, precision, recall, F1 and calibration by horizon, geography and season. Mark a version and forecast run `validated` only after review. The existing module supplies scoring/split helpers; a production feature pipeline, trainer, calibration, artifact registry and publishing job still need implementation against the chosen datasets.

## API surface currently implemented

- `GET /api/v1/health`, `/status`, `/sources`, `/locations`, `/map/locations`, `/crop-options`, `/climate/latest`
- `GET /api/v1/locations/{id}/weather-source` (raw provider output)
- `GET /api/v1/locations/{id}/historical-weather?limit=30` (processed source observations; local CSV fallback or database rows)
- `GET /api/v1/predictions/{id}?horizon=7|14|21|30` (fresh validated records only)
- `GET /api/v1/profile/me`, `PUT /api/v1/profile/me`, `GET|PUT /api/v1/profile/me/notifications`
- `GET /api/v1/advisories/{location_id}`, `/model/info`
- `GET /api/v1/admin/summary`, `/admin/data-status` (Supabase bearer + database role required)

There is intentionally no public OTP-request API; the configured Supabase client owns email confirmation, password sign-in and password recovery. `POST /api/v1/auth/otp/request` returns disabled so a second insecure authentication path is not accidentally introduced.

## Inputs needed to make it operational

- A Supabase project with email confirmation and redirect URLs configured.
- A separate Postgres/PostGIS connection.
- Licensed/authoritative Bihar boundary data with source and hierarchy IDs.
- Crop calendar/advisory source and authorization to publish the guidance.
- Copernicus token if ERA5 observations are needed; and confirmation of the applicable Open-Meteo service terms/customer endpoint for deployment.
- Historical weather/outcome dataset plus expert validation before MAUSAM model predictions can be displayed.
- Provider-approved SMS credentials/templates for outbound alerts; optional WhatsApp Business and browser push setup.

Use `.env.example` as a names-only template. Do not put credentials in the Vite bundle or Git.
