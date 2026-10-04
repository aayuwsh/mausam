# MAUSAM three-district rainfall model

This model is restricted to the three requested Bihar districts: East Champaran (official LGD boundary label: Purbi Champaran), Muzaffarpur, and Patna. It trains from user-supplied IMD daily gridded rainfall aggregated by area-weighted polygon intersection to those districts. Monthly ONI and DMI values enter as prior-calendar-month features; rainfall rolling sums, same-calendar-date historical anomaly, consecutive dry-day counts, seasonal cycle, and district representative coordinates are also used.

The ERA5-labelled files cover only a small area in Bihar and have ambiguous GRIB product metadata. They are excluded from the trained model. The separate current-location feature uses a weather provider to retrieve current conditions and a seven-day outlook at the user's browser coordinates; it is available anywhere and is distinct from model output.

`district_predictions.csv` contains model outputs for four horizons at the latest available rainfall date. The supplied IMD archive ends on 2025-12-31, so outputs are archived forecasts, not a current October 2026 forecast. Selected sub-districts receive the parent district estimate, visibly labeled as district resolution. Validation is chronological: train issue dates before 2024 and evaluate on 2024–2025. This is not operational validation.

Crop recommendations are withheld. The existing 2013 crop calendar is reference-only/unapproved and does not contain verified crop rainfall thresholds. Monsoon onset and break are also not predicted because source-supported labels are unavailable.

Regenerate the model from the project root:

```sh
npm run train-model
```

Regenerate boundaries plus model:

```sh
npm run process-national-model
```

Import nationwide boundaries into the existing PostGIS database only after applying migrations 001–006:

```sh
npm run import-national-boundaries
```
