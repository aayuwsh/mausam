# Weather and crop calendar integration

## Existing architecture

- Frontend: React 18 and Vite (`src/main.jsx`, `src/style.css`, `src/i18n.js`).
- API: FastAPI in `backend/app/main.py`.
- Database: existing optional PostgreSQL/PostGIS/Supabase-backed storage. The local weather and crop reference views work without a database; reviewed crop-calendar approval remains database-backed.
- Geography: supplied Bihar GeoJSON at `backend/data/geography/mausam/bihar/`; three district polygons and 66 block polygons are used by the weather page.

## Block weather

The weather page now uses a district → block selection. The API resolves the selected block's supplied polygon, obtains a representative point inside the polygon, and calls the Open-Meteo forecast endpoint at those coordinates. It asks for current temperature, feels-like temperature, humidity, precipitation, WMO condition code, wind speed and direction, cloud cover, surface pressure, shortwave radiation, soil temperature/moisture, and reference ET₀. The daily response supplies a 16-day array; the interface presents the first ten days. Values and units are passed through from the provider and missing fields display as unavailable. The location, coordinates, source, retrieval time, and forecast disclaimer are shown.

Open-Meteo documents access to forecasts of up to 16 days and lists soil temperature/moisture among hourly variables. Shortwave radiation is a preceding-hour mean in W/m² and daily shortwave radiation is a total in MJ/m². Variable availability can differ by weather model; absent values remain blank. See the [forecast API documentation](https://open-meteo.com/en/docs).

This is a live provider forecast, not an inference from the historical Mausam model. A separate **Historical-data model reference** now runs the saved rainfall models for the selected area using that area's most recent prior-year record for the same calendar date, including supplied IMD rainfall features and lagged ONI/DMI. It reports accumulated-rainfall estimates for 7/14/21/30 days together with chronological held-out MAE and the seasonal baseline MAE. The reference is explicitly not a current operational forecast: supplied observations end on 2025-12-31, and the model has no matched Open-Meteo hindcast validation. The live forecast remains the operational weather source.

## Crop calendar files

The three user-supplied CSVs are preserved under `data/raw/agriculture/`. A unified local view is written to `data/agriculture/crop_calendar_supplied.csv`; its manifest is `data/agriculture/crop_calendar_supplied_manifest.json`. This contains 30 source rows (10 per district) across five crop names. The original sowing/harvest strings and source text are retained. No dates, crop tolerances, or yield requirements are synthesized.

The API returns the calendar for the block's parent district. It is labeled source-reference-only unless an approved database record exists. The interface explicitly states that a calendar window alone does not establish weather suitability.

The advisory page also resolves nationwide LGD subdistrict IDs through their parent district, so a location such as Muzaffarpur → Katra uses Muzaffarpur's supplied calendar. It loads the selected subdistrict's real Open-Meteo forecast. Calendar rows whose sowing period is open or near are shown as **calendar candidates**, not confirmed crop recommendations. The farmer view combines those calendar dates with the forecast and gives a clear “not enough evidence to confirm sowing suitability” message where crop requirements are absent.

Weather precautions use transparent signals: forecast daily rain of at least 64.5 mm is flagged against IMD's heavy-rain threshold; thunderstorm WMO codes or forecast gusts at/above 40 km/h trigger a preparation note; and a forecast with no rainfall in the first seven days is described as “no rain forecast,” never as drought. For likely heavy rain, the page advises drainage and safeguarding harvested produce; for thunder/gusts, it advises securing harvested produce and supporting vulnerable plants. These actions follow the IMD Bihar agromet guidance. The page links the IMD local-language district bulletin and its Meghdoot app video tutorial. These are preparation suggestions, not official warnings.

Known input gaps remain visible: 21 records have no duration, and one record has no complete sowing window. Supplied strings are preserved even where a date should be reviewed (for example, a 31st day in a 30-day month). The calendar is not silently corrected. The listed CSVs contain no verified crop temperature/rainfall/moisture suitability thresholds, so a crop-specific “sow now” decision and crop-specific protection advice are not generated.

## Run and validate

```sh
npm run api
npm run dev
```

For a production frontend build and backend tests:

```sh
npm run build
.venv/bin/python -m pytest backend/tests -q
```

## Current limitations

- The current live ten-day view uses Open-Meteo. No bias-corrected AI/Open-Meteo ensemble is claimed.
- The historical Mausam model reference is rainfall-focused, uses a prior-year same-date feature row, and ends in 2025; it does not provide current ten-day temperature, wind, or humidity inference.
- Weather-risk thresholds, crop-specific weather suitability, and authoritative intervention guidance are not inferred from the supplied calendar alone. They require documented crop thresholds and approved guidance sources.
- The present crop data supports date-window candidates, not definitive crop suitability. The IMD resources are linked for the latest locally issued operational advice; local bulletins can change more quickly than this application.
- Soil and ET₀ fields are requested, but can be absent when the provider's selected model does not return them; the interface leaves those fields blank.
- User calendar references remain unapproved in the database until an authorized reviewer approves/imports them.
