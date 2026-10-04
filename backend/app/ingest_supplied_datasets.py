"""Inspect and process the real, user-supplied Mausam climate datasets.

Raw files are read-only inputs. Outputs are source-derived CSV plus an inventory
and validation report. Rainfall remains IMD-derived; ERA precipitation is never
substituted. This first version aggregates the supplied supported subdistrict
polygons using cosine-latitude-corrected polygon/cell intersection areas.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import math
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Polygon, shape

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw"
OUT = ROOT / "data/processed/supplied"
GEO = ROOT / "backend/data/geography/mausam/bihar"
MISSING = {"_FillValue", "missing_value"}


def clean(v: Any) -> Any:
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (np.floating,)): return float(v) if np.isfinite(v) else None
    if isinstance(v, (np.bool_,)): return bool(v)
    if isinstance(v, (np.ndarray,)): return v.tolist()
    if isinstance(v, (np.datetime64, pd.Timestamp)): return str(pd.Timestamp(v).date())
    if isinstance(v, dict): return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [clean(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None: return v
    return str(v)


def dataset_inventory() -> dict[str, Any]:
    inventory: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "processing_version":"mausam-supplied-ingest-1.0", "download_date":"unknown (not embedded reliably in the supplied source files)", "datasets": []}
    groups = [("Supplied ERA5/ERA5-Land NetCDF", RAW/"era5"/"**/*.nc"),
              ("IMD daily gridded rainfall", RAW/"imd_rainfall"/"*.nc"),
              ("NOAA ONI", RAW/"climate_indices"/"oni.nc")]
    for title, pattern in groups:
        files = sorted(RAW.glob(str(pattern).replace(str(RAW)+"/", ""))) if "**" in str(pattern) else sorted((RAW/pattern).parent.glob((RAW/pattern).name))
        entry: dict[str, Any] = {"dataset": title, "files": [str(p.relative_to(ROOT)) for p in files], "file_count": len(files)}
        entry["provenance"]={"source":"as attributed/named in user-supplied files; upstream URL and download date were not supplied","raw_files_unchanged":True,"units_and_variables":"inspected from file metadata"}
        times=[]; variables={}; coords={}; resolutions=set(); bounds=[]; nonfinite=Counter(); finite_counts=Counter(); ranges={}
        for p in files:
            try:
                with xr.open_dataset(p, decode_times=True) as ds:
                    time_key = next((x for x in ds.variables if x.lower() in {"valid_time", "time", "date"}), None)
                    if time_key:
                        tv=np.asarray(ds[time_key].values).reshape(-1)
                        times.extend([pd.Timestamp(x) for x in tv if not pd.isna(x)])
                    lat=next((x for x in ds.variables if x.lower() in {"latitude","lat"}),None); lon=next((x for x in ds.variables if x.lower() in {"longitude","lon"}),None)
                    if lat and lon:
                        la=np.asarray(ds[lat].values); lo=np.asarray(ds[lon].values)
                        if la.ndim==1 and len(la)>1 and lo.ndim==1 and len(lo)>1:
                            resolutions.add((round(float(np.median(np.abs(np.diff(la)))),8),round(float(np.median(np.abs(np.diff(lo)))),8)))
                        bounds.append([float(np.nanmin(la)),float(np.nanmax(la)),float(np.nanmin(lo)),float(np.nanmax(lo))])
                        coords[lat]={"units":ds[lat].attrs.get("units"),"count":len(la)}; coords[lon]={"units":ds[lon].attrs.get("units"),"count":len(lo)}
                    for name,var in ds.data_vars.items():
                        variables[name]={"units":var.attrs.get("units"),"long_name":var.attrs.get("long_name"),"standard_name":var.attrs.get("standard_name"),"attributes":{k:clean(v) for k,v in var.attrs.items() if k in MISSING or k.startswith("GRIB_")}}
                        a=np.asarray(var.values); finite=a[np.isfinite(a)]
                        nonfinite[name]+=int(a.size-finite.size)
                        finite_counts[name]+=int(finite.size)
                        if finite.size:
                            mm=ranges.setdefault(name,[None,None]); mm[0]=float(finite.min()) if mm[0] is None else min(mm[0],float(finite.min())); mm[1]=float(finite.max()) if mm[1] is None else max(mm[1],float(finite.max()))
            except Exception as e:
                entry.setdefault("read_errors",[]).append({"file":str(p.relative_to(ROOT)),"error":str(e)})
        if times:
            ts=sorted(set(times)); entry["time_start"]=str(ts[0].date()); entry["time_end"]=str(ts[-1].date()); entry["unique_time_records"]=len(ts)
            if all(x.hour==0 for x in ts):
                expected=pd.date_range(ts[0].normalize(),ts[-1].normalize(),freq="D"); present={x.normalize() for x in ts}; entry["missing_calendar_dates"]=[str(x.date()) for x in expected if x not in present]
        entry["coordinates"]=coords; entry["spatial_resolution_degrees"]=sorted([list(x) for x in resolutions]); entry["spatial_bounds_latlon"]=([min(b[0] for b in bounds),max(b[1] for b in bounds),min(b[2] for b in bounds),max(b[3] for b in bounds)] if bounds else None)
        entry["variables"]=variables; entry["finite_values_by_variable"]=dict(finite_counts); entry["nonfinite_values_by_variable"]=dict(nonfinite); entry["finite_value_ranges"]=ranges
        inventory["datasets"].append(entry)
    dmi=RAW/"climate_indices/dmi.had.long.csv"
    if dmi.exists():
        frame=pd.read_csv(dmi)
        vals=pd.to_numeric(frame.iloc[:,1],errors="coerce").replace(-9999,np.nan)
        dates=pd.to_datetime(frame.iloc[:,0],errors="coerce")
        inventory["datasets"].append({"dataset":"DMI (supplied HadISST CSV)","files":[str(dmi.relative_to(ROOT))],"format":"CSV","columns":list(frame.columns),"time_start":str(dates.min().date()),"time_end":str(dates.max().date()),"last_valid_date":str(dates[vals.notna()].max().date()) if vals.notna().any() else None,"records":len(frame),"valid_values":int(vals.notna().sum()),"missing_values":int(vals.isna().sum()),"value_range":[float(vals.min()),float(vals.max())],"provenance":{"source":"HadISST attribution in supplied filename/columns; upstream URL and download date not supplied","units":"index value; file has no explicit units"}})
    for p in sorted((RAW/"administrative_boundaries").glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            entry={"dataset":"LGD administrative tables" if "downloadDir" in p.name else "state capital point boundary", "file":str(p.relative_to(ROOT)),"members":z.namelist()}
            inventory["datasets"].append(entry)
    project_geometries=[]
    for level,filename in (("district","districts.geojson"),("block","blocks.geojson"),("subdistrict","subdistricts.geojson")):
        path=GEO/filename; collection=json.loads(path.read_text(encoding="utf-8")); fs=collection.get("features",[])
        ids=[str(f.get("properties",{}).get("id", "")) for f in fs]
        project_geometries.append({"level":level,"file":str(path.relative_to(ROOT)),"features":len(fs),"unique_official_ids":len(set(ids)),"codes":"LGD codes present in feature properties"})
    inventory["datasets"].append({"dataset":"Active Mausam administrative geometries","files":[x["file"] for x in project_geometries],"levels":project_geometries,"crs":"GeoJSON longitude/latitude (WGS 84 coordinate convention)","provenance":{"source":"existing boundary files in the current Mausam project","used_for_weather_aggregation":True}})
    boundary=RAW/"administrative_boundaries/district_nwic.GeoJSON"
    if boundary.exists():
        geo=json.loads(boundary.read_text(encoding="utf-8")); inventory["datasets"].append({"dataset":"Supplied NWIC district GeoJSON","file":str(boundary.relative_to(ROOT)),"format":"GeoJSON","features":len(geo.get("features",[])),"declared_crs":geo.get("crs"),"note":"Not used for processing: coordinates are projected and the file contains no declared CRS metadata."})
    return inventory


_WEIGHT_CACHE: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
_ADMIN_FEATURES: list[dict[str, Any]] | None = None

def administrative_features() -> list[dict[str, Any]]:
    global _ADMIN_FEATURES
    if _ADMIN_FEATURES is None:
        _ADMIN_FEATURES=[]
        for level,filename in (("district","districts.geojson"),("block","blocks.geojson"),("subdistrict","subdistricts.geojson")):
            data=json.loads((GEO/filename).read_text(encoding="utf-8"))
            for feature in data.get("features",[]):
                feature.setdefault("properties",{}).setdefault("mausam_level",level)
                _ADMIN_FEATURES.append(feature)
    return _ADMIN_FEATURES

def grid_weights(lat: np.ndarray, lon: np.ndarray, features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lat=np.asarray(lat,float); lon=np.asarray(lon,float)
    cache_key=(len(lat),len(lon),float(lat[0]),float(lat[-1]),float(lon[0]),float(lon[-1]),float(np.median(np.abs(np.diff(lat)))),float(np.median(np.abs(np.diff(lon)))))
    if cache_key in _WEIGHT_CACHE: return _WEIGHT_CACHE[cache_key]
    dy=float(np.median(np.abs(np.diff(lat)))); dx=float(np.median(np.abs(np.diff(lon))))
    if not np.allclose(np.abs(np.diff(lat)),dy) or not np.allclose(np.diff(lon),dx): raise ValueError("Only rectilinear regular lat/lon grids are supported")
    centers=[(float(y),float(x)) for y in lat for x in lon]
    cells=[Polygon([(x-dx/2,y-dy/2),(x+dx/2,y-dy/2),(x+dx/2,y+dy/2),(x-dx/2,y+dy/2)]) for y,x in centers]
    out={}
    for f in features:
        p=f["properties"]; key=str(p.get("id") or "")
        g=shape(f["geometry"])
        if not key or g.is_empty or not g.is_valid: raise ValueError(f"Invalid administrative geometry or id: {key}")
        ids=[]; weights=[]
        for i,c in enumerate(cells):
            if g.intersects(c):
                a=g.intersection(c).area*math.cos(math.radians(centers[i][0]))
                if a>0: ids.append(i); weights.append(a)
        if ids: out[key]={"indices":np.array(ids),"weights":np.array(weights),"properties":p,"cells":len(ids)}
    _WEIGHT_CACHE[cache_key]=out
    return out


def daily_netcdf(root: Path, primary: bool) -> pd.DataFrame:
    all_rows=[]; expected_files=list(root.rglob("*.nc")); rejected=Counter(); seen=set()
    if not expected_files: raise FileNotFoundError(f"No NetCDF files under {root}")
    for p in sorted(expected_files):
        with xr.open_dataset(p,decode_times=True) as ds:
            latk=next((x for x in ds.variables if x.lower() in {"latitude","lat"}),None); lonk=next((x for x in ds.variables if x.lower() in {"longitude","lon"}),None)
            tk=next((x for x in ds.variables if x.lower() in {"valid_time","time","date"}),None)
            if not (latk and lonk and tk): raise ValueError(f"{p}: expected latitude, longitude and time coordinates, got {list(ds.coords)}")
            vars_=list(ds.data_vars)
            if primary:
                vk=next((x for x in vars_ if x.lower() in {"rainfall","rf","precipitation"}),None)
                if vk is None: raise ValueError(f"{p}: no recognized rainfall variable found; detected {vars_}")
                if str(ds[vk].attrs.get("units","")).lower() not in {"mm","millimeter","millimetre","mm/day","mm d-1"}:
                    raise ValueError(f"{p}: unsupported rainfall units {ds[vk].attrs.get('units')!r}")
                selected={"rainfall_mm":vk}
            else:
                # Include only variables present, preserving meanings from source names.
                aliases={"t2m":"temperature_snapshot_c","d2m":"dewpoint_snapshot_c","swvl1":"soil_moisture_layer1","swvl2":"soil_moisture_layer2","u10":"wind_u","v10":"wind_v","sp":"surface_pressure_snapshot_pa"}
                selected={out:name for name in vars_ if (out:=aliases.get(name.lower()))}
                if not selected: raise ValueError(f"{p}: no supported ERA variable found; detected {vars_}")
                for src in selected.values():
                    a=ds[src].attrs
                    if src.lower() in {"t2m","d2m"} and str(a.get("units","")).lower() not in {"k","kelvin"}: raise ValueError(f"{p}: unsupported {src} units {a.get('units')!r}")
            lat=np.asarray(ds[latk].values,float); lon=np.asarray(ds[lonk].values,float)
            areas=grid_weights(lat,lon,administrative_features())
            tvals=np.asarray(ds[tk].values).reshape(-1)
            for ti,t in enumerate(tvals):
                day=str(pd.Timestamp(t).date())
                if (day,primary) in seen: raise ValueError(f"Duplicate {'IMD' if primary else 'ERA'} date across files: {day}")
                seen.add((day,primary))
                fields={out:np.asarray(ds[src].isel({tk:ti}).values,float).reshape(-1) for out,src in selected.items()}
                for lid,area in areas.items():
                    row={"date":day,"admin_level":area["properties"].get("mausam_level",area["properties"].get("level","unknown")),"admin_unit_id":lid,"spatial_grid_cells_"+("imd" if primary else "era5"):area["cells"]}
                    for field,values in fields.items():
                        vals=values[area["indices"]]; valid=np.isfinite(vals)&np.isfinite(area["weights"])
                        row[field]=float(np.average(vals[valid],weights=area["weights"][valid])) if valid.any() else np.nan
                        rejected[field]+=int((~np.isfinite(vals)).sum())
                        if field in {"temperature_snapshot_c","dewpoint_snapshot_c"}: row[field]=row[field]-273.15 if np.isfinite(row[field]) else np.nan
                    all_rows.append(row)
    frame=pd.DataFrame(all_rows)
    if frame.empty: raise ValueError(f"No valid rows processed under {root}")
    frame=frame.sort_values(["admin_unit_id","date"])
    return frame


def climate_indices() -> pd.DataFrame:
    oni_path=RAW/"climate_indices/oni.nc"; dmi_path=RAW/"climate_indices/dmi.had.long.csv"
    if not oni_path.is_file() or not dmi_path.is_file(): raise FileNotFoundError("Supplied ONI or DMI file is missing")
    with xr.open_dataset(oni_path,decode_times=True) as ds:
        tk=next((x for x in ("time","date") if x in ds.coords),None); vk=next((x for x in ("value","oni","ONI") if x in ds.data_vars),None)
        if not tk or not vk: raise ValueError(f"ONI schema unexpected; coords={list(ds.coords)}, variables={list(ds.data_vars)}")
        oni=pd.DataFrame({"month":pd.to_datetime(ds[tk].values).to_period("M").astype(str),"ONI":np.asarray(ds[vk].values).reshape(-1)})
    if oni.month.duplicated().any(): raise ValueError("Duplicate monthly ONI observations")
    dmi0=pd.read_csv(dmi_path)
    if len(dmi0.columns)<2: raise ValueError("DMI CSV needs date and value columns")
    dmi=pd.DataFrame({"month":pd.to_datetime(dmi0.iloc[:,0],errors="coerce").dt.to_period("M").astype(str),"DMI":pd.to_numeric(dmi0.iloc[:,1],errors="coerce").replace(-9999,np.nan)})
    dmi=dmi.dropna(subset=["month"])
    if dmi.month.duplicated().any(): raise ValueError("Duplicate monthly DMI observations")
    out=oni.merge(dmi,on="month",how="outer",validate="one_to_one").sort_values("month")
    out["DMI_3M"]=out.DMI.rolling(3,min_periods=3).mean()
    for lag in (1,2,3): out[f"DMI_lag_{lag}"]=out.DMI.shift(lag)
    return out


def lgd_inventory(output: Path) -> dict[str, Any]:
    files=[]
    for zpath in (RAW/"administrative_boundaries").glob("*.zip"):
        with zipfile.ZipFile(zpath) as z:
            for member in z.namelist():
                if not member.lower().endswith((".xls",".xml")): continue
                root=ET.fromstring(z.read(member)); rows=[]
                for row in root.iter():
                    if row.tag.endswith("Row"):
                        vals=[]
                        for cell in row:
                            if cell.tag.endswith("Cell"):
                                data=next((x for x in cell if x.tag.endswith("Data")),None); vals.append(data.text if data is not None else "")
                        if vals: rows.append(vals)
                stem=Path(member).name
                if stem.startswith("districtofSpecificState"): kind="districts"; headers=["serial","district_code","district_version","district_name_en","district_name_local","census_2001_code","census_2011_code"]; start=5
                elif stem.startswith("blockofspecificState"): kind="blocks"; headers=["serial","district_code","district_name_en","block_code","block_version","block_name_en","block_name_local"]; start=5
                elif stem.startswith("priLbSpecificState"): kind="local_bodies"; headers=["serial","localbody_type_code","localbody_type_name_en","localbody_code","localbody_version","localbody_name_en","localbody_name_local","parent_localbody_code"]; start=5
                elif stem.startswith("villageGramPanchayatMapping"): kind="village_panchayat_mapping"; headers=[re.sub(r"[^a-z0-9]+","_",str(x or "").strip().lower()).strip("_") or f"column_{i+1}" for i,x in enumerate(rows[4])]; start=6
                else: continue
                records=[]
                for row in rows[start:]:
                    values=(row+([""]*len(headers)))[:len(headers)]
                    if values and any(v not in (None,"") for v in values): records.append(dict(zip(headers,values)))
                target=output/f"lgd_{kind}.csv"
                if records:
                    with target.open("w",newline="",encoding="utf-8") as handle:
                        writer=csv.DictWriter(handle,fieldnames=headers); writer.writeheader(); writer.writerows(records)
                files.append({"archive":zpath.name,"member":member,"header_row":headers,"records":len(records),"processed_file":str(target.relative_to(ROOT)) if target.exists() else None})
    return {"files":files,"note":"Official LGD codes and table relationships are retained in processed CSV. Village/Gram Panchayat mapping is attribute hierarchy only; no supplied Panchayat polygons were found for weather aggregation."}


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("--inventory-only",action="store_true"); ap.add_argument("--import-db",action="store_true",help="Import processed rows to configured DATABASE_URL after migration 005 has been applied."); ap.add_argument("--output-dir",type=Path,default=OUT); args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    inventory=dataset_inventory(); inventory["lgd_tables"]=lgd_inventory(args.output_dir)
    (args.output_dir/"data_inventory.json").write_text(json.dumps(clean(inventory),indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    if args.inventory_only:
        print(json.dumps({"inventory":str(args.output_dir/"data_inventory.json"),"datasets":len(inventory["datasets"])})); return
    rainfall=daily_netcdf(RAW/"imd_rainfall",True)
    era=daily_netcdf(RAW/"era5",False)
    indices=climate_indices()
    key=["date","admin_level","admin_unit_id"]
    features=rainfall.merge(era,on=key,how="outer",validate="one_to_one",suffixes=("","_era"))
    idx=indices.copy(); idx["date"]=pd.to_datetime(idx.month+"-01"); features["month"]=pd.to_datetime(features.date).dt.to_period("M").astype(str)
    features=features.merge(idx.drop(columns="date"),on="month",how="left",validate="many_to_one").drop(columns="month")
    features=features.sort_values(["admin_unit_id","date"])
    # Attach official codes/names from supplied, verified geometry metadata.
    geos=administrative_features()
    prop={str(f["properties"]["id"]):f["properties"] for f in geos}
    for col,fn in {"state_code":lambda p:str(p.get("state_code") or ""),"state_name":lambda p:p.get("state") or "","district_code":lambda p:str(p.get("lgd") if p.get("level")=="district" else p.get("dist_lgd") or ""),"district_name":lambda p:p.get("name") if p.get("level")=="district" else p.get("district") or "","block_code":lambda p:str(p.get("lgd") or "") if p.get("mausam_level")=="block" else "","block_name":lambda p:p.get("name") or "" if p.get("mausam_level")=="block" else "","subdistrict_code":lambda p:str(p.get("lgd") or "") if p.get("mausam_level")=="subdistrict" else "","subdistrict_name":lambda p:p.get("name") or "" if p.get("mausam_level")=="subdistrict" else "","latitude":lambda p:None,"longitude":lambda p:None}.items():
        features[col]=features.admin_unit_id.map(lambda i:fn(prop.get(i,{})))
    centroids={str(f["properties"]["id"]):shape(f["geometry"]).representative_point() for f in geos}
    features["latitude"]=features.admin_unit_id.map(lambda i:centroids[i].y if i in centroids else np.nan)
    features["longitude"]=features.admin_unit_id.map(lambda i:centroids[i].x if i in centroids else np.nan)
    # Rainfall-derived predictors and labels use observations available through the row date.
    features["rainfall_mm"]=features.get("rainfall_mm")
    grouped=features.groupby("admin_unit_id",sort=False)
    for n in (3,7,14,30): features[f"rainfall_{n}d"]=grouped.rainfall_mm.transform(lambda s:s.rolling(n,min_periods=n).sum())
    def dry_run(series: pd.Series) -> pd.Series:
        count=0; values=[]
        for value in series:
            if pd.isna(value): count=0; values.append(np.nan)
            elif value < 2.5: count+=1; values.append(count)
            else: count=0; values.append(0)
        return pd.Series(values,index=series.index,dtype=float)
    features["consecutive_dry_days"]=grouped.rainfall_mm.transform(dry_run)
    # The anomaly baseline is expanding past-only by day-of-year, requiring >=1 prior observation.
    features["rainfall_anomaly"]=np.nan
    dt=pd.to_datetime(features.date); features["_doy"]=dt.dt.strftime("%m-%d")
    for _,ids in features.groupby("admin_unit_id").groups.items():
        sub=features.loc[ids].sort_values("date")
        means=sub.groupby("_doy").rainfall_mm.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
        features.loc[sub.index,"rainfall_anomaly"]=sub.rainfall_mm.to_numpy()-means.to_numpy()
    # Future targets require a complete, observed next horizon; never backfill missing data.
    for h in (7,14,21,30):
        features[f"target_rain_{h}d"]=grouped.rainfall_mm.transform(lambda s:s.shift(-1).rolling(h,min_periods=h).sum().shift(-(h-1)))
    # labels describe observed source events; probabilities remain unavailable without a validated model.
    features["heavy_rain_label"]=features.rainfall_mm.ge(64.5).where(features.rainfall_mm.notna())
    features["dry_spell_label"]=features.target_rain_7d.lt(2.5).where(features.target_rain_7d.notna())
    features["onset_label"]=np.nan; features["break_label"]=np.nan
    features=features.drop(columns=["_doy"])
    # stable, transparent record validation
    duplicate=features.duplicated(key,keep=False); rejected=int(duplicate.sum())
    if rejected: raise ValueError(f"Duplicate feature keys found: {rejected} rows")
    features_path=args.output_dir/"mausam_features.csv.gz"
    features.to_csv(features_path,index=False,na_rep="",compression="gzip")
    # Remove the uncompressed output written by the earlier pipeline revision.
    (args.output_dir/"mausam_features.csv").unlink(missing_ok=True)
    indices.to_csv(args.output_dir/"climate_indices_monthly.csv",index=False,na_rep="")
    # Explicit Parquet status; no unsupported format is mislabeled.
    try:
        features.to_parquet(args.output_dir/"mausam_features.parquet",index=False)
        parquet="written"
    except (ImportError, ModuleNotFoundError) as e:
        parquet=f"unavailable: {e}"
    summary={"status":"processed","source":{"rainfall":"IMD daily gridded rainfall","era5":"supplied ERA5/ERA5-Land-labelled data; single 10:00 UTC snapshot/day"},"records":len(features),"rejected_duplicate_records":rejected,"missing_by_field":{c:int(features[c].isna().sum()) for c in features.columns if features[c].isna().any()},"date_range":[str(features.date.min()),str(features.date.max())],"administrative_units_by_level":{str(k):int(v) for k,v in features.groupby("admin_level").admin_unit_id.nunique().items()},"administrative_units":int(features.admin_unit_id.nunique()),"parquet":parquet,"output":"gzip-compressed CSV because no Parquet engine is installed; pyarrow dependency is declared","rain_threshold_mm":2.5,"heavy_rain_threshold_mm":64.5,"label_definitions":{"heavy_rain_label":"observed date has IMD rainfall >=64.5 mm","dry_spell_label":"following 7-day observed IMD rainfall sum <2.5 mm; null unless all 7 daily values are present","onset_label":"not defined/generated: provided sources do not include all required official onset indicators","break_label":"not defined/generated: no supplied operational break-monsoon definition/source set"},"future_targets":"sum of the next 7/14/21/30 observed daily IMD records; null unless the full horizon exists","anomaly":"expanding same-month-day prior-year baseline only; no current/future values included","climate_index_temporal_caveat":"ONI and DMI values are aligned to their calendar month as requested; publication/release dates were not supplied, so index values must be lagged/availability-checked before real-time training to avoid publication-time leakage","onset_break":"not generated: supplied sources do not support a defensible monsoon onset/break definition"}
    (args.output_dir/"processing_report.json").write_text(json.dumps(clean(summary),indent=2)+"\n",encoding="utf-8")
    if args.import_db:
        asyncio.run(import_database(features_path, args.output_dir/"climate_indices_monthly.csv"))
    print(json.dumps(summary,indent=2))


async def import_database(features_path: Path, indices_path: Path) -> None:
    """Bulk-import processed local rows into the existing Supabase schema."""
    from .config import settings
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured. Apply migration 005 and configure the existing Supabase database before using --import-db.")
    from .db_connection import connect_database
    con=await connect_database(settings.database_url)
    try:
        async with con.transaction():
            source_ids=["imd_user_gridded_rainfall_2020_2025","era5_user_land_2020_2025","noaa_oni_user_file","dmi_hadisst_user_file","mausam_supplied_multisource_2020_2025"]
            found=await con.fetch("SELECT id FROM data_sources WHERE id=ANY($1::text[])",source_ids)
            if {r["id"] for r in found}!=set(source_ids): raise RuntimeError("Migration 005 data_sources entries are missing; apply the migration first.")
            # Supplied weather files use IDs like IN-BR-D-208; the operational
            # PostGIS table uses IN-LGD-D-10-208. Resolve by official LGD code
            # and hierarchy instead of assuming those local IDs are identical.
            location_rows=await con.fetch("""SELECT id,level,district_code,block_code,subdistrict_code
              FROM locations WHERE active=true AND state_code='10'
                AND district_code=ANY($1::text[])""",["208","212","213"])
            location_by_code={}
            code_column={"district":"district_code","block":"block_code","subdistrict":"subdistrict_code"}
            for loc in location_rows:
                field=code_column.get(loc["level"])
                code=loc[field] if field else None
                if code:
                    location_by_code[(loc["level"],str(code))]=loc["id"]
            columns=["location_id","date","rainfall_mm","rainfall_3d_mm","rainfall_7d_mm","rainfall_14d_mm","rainfall_30d_mm","rainfall_anomaly_mm","consecutive_dry_days","temperature_snapshot_10utc_c","dewpoint_snapshot_10utc_c","surface_pressure_snapshot_10utc_pa","soil_moisture_layer1","soil_moisture_layer2","wind_u_mean_ms","wind_v_mean_ms","oni","dmi","dmi_3m","target_rain_7d_mm","target_rain_14d_mm","target_rain_21d_mm","target_rain_30d_mm","observed_heavy_rain_label","observed_dry_spell_label","spatial_grid_cells","source_id"]
            mapping={"rainfall_mm":"rainfall_mm","rainfall_3d":"rainfall_3d_mm","rainfall_7d":"rainfall_7d_mm","rainfall_14d":"rainfall_14d_mm","rainfall_30d":"rainfall_30d_mm","rainfall_anomaly":"rainfall_anomaly_mm","consecutive_dry_days":"consecutive_dry_days","temperature_snapshot_c":"temperature_snapshot_10utc_c","dewpoint_snapshot_c":"dewpoint_snapshot_10utc_c","surface_pressure_snapshot_pa":"surface_pressure_snapshot_10utc_pa","soil_moisture_layer1":"soil_moisture_layer1","soil_moisture_layer2":"soil_moisture_layer2","wind_u":"wind_u_mean_ms","wind_v":"wind_v_mean_ms","ONI":"oni","DMI":"dmi","DMI_3M":"dmi_3m","target_rain_7d":"target_rain_7d_mm","target_rain_14d":"target_rain_14d_mm","target_rain_21d":"target_rain_21d_mm","target_rain_30d":"target_rain_30d_mm","heavy_rain_label":"observed_heavy_rain_label","dry_spell_label":"observed_dry_spell_label","spatial_grid_cells_era5":"spatial_grid_cells","spatial_grid_cells_imd":"spatial_grid_cells"}
            records=[]
            with gzip.open(features_path,"rt",encoding="utf-8",newline="") as h:
                for r in csv.DictReader(h):
                    def val(field: str):
                        source=next((k for k,v in mapping.items() if v==field and r.get(k) not in (None,"")),None)
                        if source is None: return None
                        raw=r[source]
                        if field=="date": return datetime.fromisoformat(raw).date()
                        if field in {"location_id","source_id"}: return raw
                        if field in {"consecutive_dry_days","spatial_grid_cells"}: return int(float(raw))
                        if field in {"observed_heavy_rain_label","observed_dry_spell_label"}: return raw.lower()=="true"
                        return float(raw)
                    code_field={"district":"district_code","block":"block_code","subdistrict":"subdistrict_code"}.get(r["admin_level"])
                    code=(r.get(code_field) or "").strip() if code_field else ""
                    location_id=location_by_code.get((r["admin_level"],code))
                    if not location_id:
                        raise RuntimeError(f"No active supported LGD location matches level={r['admin_level']} code={code} ({r['admin_unit_id']})")
                    row=[location_id,datetime.fromisoformat(r["date"]).date()]
                    for field in columns[2:-2]: row.append(val(field))
                    cells=r.get("spatial_grid_cells_era5") or r.get("spatial_grid_cells_imd") or "1"
                    row.extend([int(float(cells)),"mausam_supplied_multisource_2020_2025"])
                    records.append(tuple(row))
            await con.executemany(f"""INSERT INTO weather_daily ({','.join(columns)}) VALUES ({','.join(f'${i}' for i in range(1,len(columns)+1))})
                ON CONFLICT(location_id,date,source_id) DO UPDATE SET {','.join(f'{c}=EXCLUDED.{c}' for c in columns[2:-1])}""",records)
            with indices_path.open(encoding="utf-8",newline="") as h:
                index_rows=list(csv.DictReader(h))
            climate_records=[]
            for r in index_rows:
                month=datetime.strptime(r["month"],"%Y-%m").date()
                for name,value,source in (("ONI",r.get("ONI"),"noaa_oni_user_file"),("DMI",r.get("DMI"),"dmi_hadisst_user_file"),("DMI_3M",r.get("DMI_3M"),"dmi_hadisst_user_file")):
                    if value in (None,""): continue
                    climate_records.append((name,month,float(value),json.dumps({"units":"index value","raw_month":"monthly; aligned to calendar month, no interpolation"}),source))
            if climate_records:
                await con.executemany("""INSERT INTO climate_indices(index_name,valid_at,value_1,details,source_id,retrieved_at)
                  VALUES($1,$2,$3,$4::jsonb,$5,now()) ON CONFLICT(index_name,valid_at,source_id)
                  DO UPDATE SET value_1=EXCLUDED.value_1,details=EXCLUDED.details,retrieved_at=now()""",climate_records)
            await con.execute("UPDATE data_sources SET configured=true,last_status='processed',last_processed_at=now() WHERE id=ANY($1::text[])",source_ids)
    finally:
        await con.close()


if __name__=="__main__":
    main()
