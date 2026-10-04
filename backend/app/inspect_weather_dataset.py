"""Print NetCDF/GRIB metadata needed to design a safe weather import.

This audit intentionally reads coordinates and metadata only. It does not ingest,
aggregate, or publish observations.
"""
import argparse
import json
from pathlib import Path

import xarray as xr


def scalar(value):
    if hasattr(value, "item"):
        value = value.item()
    return str(value)


def inspect(path: Path) -> dict:
    with xr.open_dataset(path, engine="h5netcdf", decode_times=True) as ds:
        coords = {}
        for name, coord in ds.coords.items():
            if coord.size > 1000:
                continue
            values = coord.values
            coords[name] = {
                "dims": list(coord.dims),
                "size": int(coord.size),
                "first": scalar(values.flat[0]) if values.size else None,
                "last": scalar(values.flat[-1]) if values.size else None,
                "units": coord.attrs.get("units"),
            }
        variables = {}
        for name, var in ds.data_vars.items():
            attrs = var.attrs
            variables[name] = {
                "dims": list(var.dims),
                "shape": list(var.shape),
                "units": attrs.get("units") or attrs.get("GRIB_units"),
                "standard_name": attrs.get("standard_name") or attrs.get("GRIB_cfName"),
                "long_name": attrs.get("long_name") or attrs.get("GRIB_name"),
                "short_name": attrs.get("GRIB_shortName"),
                "data_type": attrs.get("GRIB_dataType"),
                "step_type": attrs.get("GRIB_stepType"),
                "step_units": attrs.get("GRIB_stepUnits"),
            }
        return {
            "file": str(path),
            "dimensions": {k: int(v) for k, v in ds.sizes.items()},
            "coordinates": coords,
            "variables": variables,
            "global_attributes": {k: scalar(v) for k, v in ds.attrs.items()},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path, nargs="?", default=Path("backend/data"))
    parser.add_argument("--output", type=Path, help="Optional path for a JSON audit report")
    args = parser.parse_args()
    files = sorted(args.dataset_dir.rglob("*.nc"))
    if not files:
        raise SystemExit(f"No .nc files found under {args.dataset_dir}")
    report = {"file_count": len(files), "files": [inspect(path) for path in files]}
    # xarray metadata can include numpy scalar or array attribute values.
    rendered = json.dumps(report, indent=2, ensure_ascii=False, default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Inspected {len(files)} NetCDF files. Report: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
