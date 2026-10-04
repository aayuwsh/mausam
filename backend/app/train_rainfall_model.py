"""Aggregate supplied IMD rainfall nationwide, chronologically train and forecast."""
from __future__ import annotations
import argparse,csv,gzip,json,math
from datetime import date,timedelta
from pathlib import Path
from typing import Any
import joblib
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import shape
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import brier_score_loss,mean_absolute_error,mean_squared_error,roc_auc_score

from .ingest_supplied_datasets import RAW,ROOT,grid_weights

OUT=ROOT/"data/processed/national"
ADMIN=ROOT/"data/processed/administrative"
MODEL=ROOT/"backend/models/national-rainfall-v1"
FEATURE_NAMES=["rainfall_3d","rainfall_7d","rainfall_14d","rainfall_30d","rainfall_anomaly","consecutive_dry_days","ONI_lag1","DMI_lag1","DMI_3M_lag1","day_sin","day_cos","latitude","longitude"]
HORIZONS=(7,14,21,30)
MODEL_DISTRICT_IDS={"IN-LGD-D-10-213","IN-LGD-D-10-208","IN-LGD-D-10-212"}

def aggregate_imd()->tuple[np.ndarray,np.ndarray,list[dict[str,Any]],dict[str,Any]]:
    geography=ROOT/"backend/data/geography/mausam/bihar"
    try:
        district_source=json.loads((geography/"districts.geojson").read_text(encoding="utf-8"))["features"]
        subdistrict_source=json.loads((geography/"subdistricts.geojson").read_text(encoding="utf-8"))["features"]
    except (OSError,ValueError,KeyError) as exc:
        raise FileNotFoundError("Verified Bihar district and sub-district boundaries are required for spatial model training.") from exc
    scope_codes={"213","208","212"}; features=[]
    for source_feature in district_source:
        p=source_feature.get("properties",{}); code=str(p.get("lgd") or p.get("source_code") or "")
        if code in scope_codes:
            canonical={**p,"id":f"IN-LGD-D-10-{code}","lgd_code":code,"state_code":"10","state_name":"Bihar","district_code":code,"district_name":p.get("name"),"level":"district"}
            features.append({**source_feature,"properties":canonical})
    for source_feature in subdistrict_source:
        p=source_feature.get("properties",{}); code=str(p.get("dist_lgd") or ""); lgd=str(p.get("lgd") or p.get("source_code") or "")
        if code in scope_codes:
            canonical={**p,"id":f"IN-LGD-S-10-{code}-{lgd}","lgd_code":lgd,"state_code":"10","state_name":"Bihar","district_code":code,"district_name":p.get("district"),"parent_id":f"IN-LGD-D-10-{code}","level":"subdistrict"}
            features.append({**source_feature,"properties":canonical})
    district_features=[f for f in features if f["properties"]["level"]=="district"]
    subdistrict_features=[f for f in features if f["properties"]["level"]=="subdistrict"]
    if {f["properties"]["id"] for f in district_features} != MODEL_DISTRICT_IDS:
        raise ValueError("The supplied boundaries are missing one of the three model districts: East Champaran/Purbi Champaran, Muzaffarpur, Patna.")
    if len(subdistrict_features)!=66:
        raise ValueError(f"Expected 66 verified sub-district polygons across the supported districts; found {len(subdistrict_features)}.")
    areas=None; date_parts=[]; rain_parts=[]; diagnostics={"files":0,"nonfinite_source_cells":0,"grid_resolution_degrees":None}
    expected_var="RAINFALL"
    for path in sorted((RAW/"imd_rainfall").glob("*.nc")):
        with xr.open_dataset(path,decode_times=True) as ds:
            vk=next((v for v in ds.data_vars if v.lower()==expected_var.lower()),None)
            latk=next((c for c in ds.variables if c.lower() in {"latitude","lat"}),None); lonk=next((c for c in ds.variables if c.lower() in {"longitude","lon"}),None); tk=next((c for c in ds.variables if c.lower() in {"time","valid_time","date"}),None)
            if not (vk and latk and lonk and tk): raise ValueError(f"{path}: missing expected rainfall variable/coordinates; found variables={list(ds.data_vars)}, coordinates={list(ds.variables)}")
            units=str(ds[vk].attrs.get("units","")).lower()
            if units not in {"mm","millimeter","millimetre","mm/day","mm d-1"}: raise ValueError(f"{path}: IMD rainfall units are unsupported: {units!r}")
            lat=np.asarray(ds[latk].values,float); lon=np.asarray(ds[lonk].values,float)
            if areas is None:
                areas=grid_weights(lat,lon,features)
                diagnostics["grid_resolution_degrees"]=[float(np.median(np.abs(np.diff(lat)))),float(np.median(np.diff(lon)))]
            elif len(lat)!=ds.sizes[latk] or len(lon)!=ds.sizes[lonk]: raise ValueError("IMD grid dimensions changed between annual files")
            raw=np.asarray(ds[vk].values,dtype=np.float32)
            dates=pd.to_datetime(ds[tk].values).normalize().to_numpy(dtype="datetime64[D]")
            if raw.shape[0]!=len(dates): raise ValueError(f"{path}: time dimension mismatch")
            flat=raw.reshape((len(dates),-1)); diagnostics["nonfinite_source_cells"]+=int((~np.isfinite(flat)).sum()); diagnostics["files"]+=1
            daily=np.full((len(dates),len(features)),np.nan,dtype=np.float32)
            for j,feature in enumerate(features):
                area=areas[feature["properties"]["id"]]; ix=area["indices"]; w=area["weights"].astype(np.float32)
                vals=flat[:,ix]; valid=np.isfinite(vals); weighted=np.where(valid,vals*w[None,:],0).sum(axis=1); denominator=np.where(valid,w[None,:],0).sum(axis=1)
                daily[:,j]=np.divide(weighted,denominator,out=np.full(len(dates),np.nan,dtype=np.float32),where=denominator>0)
            date_parts.append(dates); rain_parts.append(daily)
    if not date_parts: raise ValueError("No IMD daily rainfall files were processed")
    dates=np.concatenate(date_parts); rain=np.concatenate(rain_parts,axis=0); order=np.argsort(dates); dates=dates[order]; rain=rain[order]
    if len(np.unique(dates))!=len(dates): raise ValueError("Duplicate IMD daily dates found across files")
    full=np.arange(dates[0],dates[-1]+np.timedelta64(1,"D"),dtype="datetime64[D]")
    if len(full)!=len(dates) or not np.array_equal(full,dates): raise ValueError("IMD dataset has missing daily dates; training requires a complete date index")
    diagnostics.update({"date_start":str(dates[0]),"date_end":str(dates[-1]),"dates":len(dates),"districts":len(district_features),"subdistricts":len(subdistrict_features),"locations":len(features),"records":int(np.isfinite(rain).sum()),"missing_location_days":int((~np.isfinite(rain)).sum())})
    # Decoded TopoJSON geometry is lon/lat; representative points supply location features.
    locs=[]
    for f in features:
        p=f["properties"]; point=shape(f["geometry"]).representative_point()
        locs.append({"id":p["id"],"lgd_code":str(p.get("lgd_code") or ""),"name":p.get("name") or "","state_code":str(p.get("state_code") or ""),"state_name":p.get("state_name") or p.get("state") or "Bihar","district_code":str(p.get("district_code") or ""),"district_name":p.get("district_name") or p.get("name") or "","admin_level":p.get("level","district"),"latitude":float(point.y),"longitude":float(point.x)})
    return dates,rain,locs,diagnostics

def feature_arrays(dates:np.ndarray,rain:np.ndarray,locations:list[dict[str,Any]],indices_path:Path):
    T,N=rain.shape; rain64=rain.astype(np.float64); frames=[]
    for window in (3,7,14,30):
        frames.append(pd.DataFrame(rain64).rolling(window,min_periods=window).sum().to_numpy(dtype=np.float32))
    anomaly=np.full((T,N),np.nan,dtype=np.float32); group={}
    for i,d in enumerate(pd.to_datetime(dates)):
        key=(d.month,d.day); prior=group.get(key,[])
        if prior: anomaly[i]=rain64[prior].mean(axis=0).astype(np.float32)
        group.setdefault(key,[]).append(i)
    anomaly=rain-anomaly
    dry=np.full((T,N),np.nan,dtype=np.float32); counts=np.zeros(N,dtype=np.int16)
    for i in range(T):
        vals=rain[i]; missing=~np.isfinite(vals); counts[missing]=0; counts[(~missing)&(vals>=2.5)]=0; counts[(~missing)&(vals<2.5)]+=1
        dry[i]=np.where(missing,np.nan,counts)
    idx=pd.read_csv(indices_path); by_month={str(r.month):r for r in idx.itertuples(index=False)}
    oni=np.full(T,np.nan,np.float32); dmi=np.full(T,np.nan,np.float32); dmi3=np.full(T,np.nan,np.float32)
    for i,d in enumerate(pd.to_datetime(dates)):
        prev=(d-pd.offsets.MonthBegin(1)).to_period("M").strftime("%Y-%m")
        if prev in by_month:
            r=by_month[prev]; oni[i]=float(r.ONI) if pd.notna(r.ONI) else np.nan; dmi[i]=float(r.DMI) if pd.notna(r.DMI) else np.nan; dmi3[i]=float(r.DMI_3M) if pd.notna(r.DMI_3M) else np.nan
    day=pd.to_datetime(dates).dayofyear.to_numpy(dtype=float); angle=2*np.pi*(day-1)/365.25
    lat=np.asarray([x["latitude"] for x in locations],np.float32); lon=np.asarray([x["longitude"] for x in locations],np.float32)
    matrices=[*frames,anomaly,dry,np.broadcast_to(oni[:,None],(T,N)),np.broadcast_to(dmi[:,None],(T,N)),np.broadcast_to(dmi3[:,None],(T,N)),np.broadcast_to(np.sin(angle)[:,None],(T,N)),np.broadcast_to(np.cos(angle)[:,None],(T,N)),np.broadcast_to(lat[None,:],(T,N)),np.broadcast_to(lon[None,:],(T,N))]
    X=np.stack(matrices,axis=-1).reshape((-1,len(FEATURE_NAMES))).astype(np.float32)
    targets={}
    for h in HORIZONS:
        target=np.full((T,N),np.nan,np.float32); heavy=np.full((T,N),np.nan,np.float32)
        for i in range(T-h):
            vals=rain[i+1:i+h+1]; complete=np.isfinite(vals).all(axis=0)
            if complete.any(): target[i,complete]=np.sum(vals[:,complete],axis=0)
            heavy[i,complete]=np.any(vals[:,complete]>=64.5,axis=0)
        targets[h]=(target.reshape(-1),heavy.reshape(-1))
    # Deterministic weekly issue dates reduce temporal redundancy without random sampling.
    selected_days=np.arange(35,T,3,dtype=int); row_ids=(selected_days[:,None]*N+np.arange(N)[None,:]).reshape(-1)
    return X,targets,row_ids

def new_regressor():
    return HistGradientBoostingRegressor(loss="poisson",learning_rate=0.07,max_iter=70,max_leaf_nodes=15,min_samples_leaf=80,l2_regularization=2.0,early_stopping=False)

def new_classifier():
    return HistGradientBoostingClassifier(learning_rate=0.07,max_iter=70,max_leaf_nodes=15,min_samples_leaf=80,l2_regularization=2.0,early_stopping=False)

def metrics(y,yp,event=None,prob=None):
    out={"mae_mm":float(mean_absolute_error(y,yp)),"rmse_mm":float(np.sqrt(mean_squared_error(y,yp))),"observations":int(len(y))}
    if event is not None and prob is not None:
        out["brier_score"]=float(brier_score_loss(event,prob)); out["event_rate"]=float(np.mean(event));
        if len(np.unique(event))>1: out["roc_auc"]=float(roc_auc_score(event,prob))
    return out

def seasonal_baseline(train_rows, train_values, query_rows, date_rows, location_count):
    """Past-only location/month climatology; train rows must already exclude target overlap."""
    values=np.asarray(train_values,dtype=float)
    loc_train=np.asarray(train_rows,dtype=int)%location_count
    month_train=pd.DatetimeIndex(date_rows[np.asarray(train_rows,dtype=int)]).month.to_numpy()
    global_mean=float(np.mean(values))
    grouped={}
    for loc,month,value in zip(loc_train,month_train,values):
        grouped.setdefault((int(loc),int(month)),[]).append(float(value))
    query=np.asarray(query_rows,dtype=int)
    loc_query=query%location_count
    month_query=pd.DatetimeIndex(date_rows[query]).month.to_numpy()
    return np.asarray([np.mean(grouped[(int(loc),int(month))]) if (int(loc),int(month)) in grouped else global_mean
                       for loc,month in zip(loc_query,month_query)],dtype=float)

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-dir",type=Path,default=OUT); parser.add_argument("--models-dir",type=Path,default=MODEL); args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True); args.models_dir.mkdir(parents=True,exist_ok=True)
    dates,rain,locations,source=aggregate_imd()
    X,targets,weekly=feature_arrays(dates,rain,locations,ROOT/"data/processed/supplied/climate_indices_monthly.csv")
    T,N=rain.shape; day_index=np.repeat(np.arange(T),N); date_rows=pd.to_datetime(dates[day_index]); valid_x=np.isfinite(X).all(axis=1)
    cutoff=pd.Timestamp("2024-01-01"); test_end=pd.Timestamp("2026-01-01")
    outputs=[]; model_reports={}
    for horizon in HORIZONS:
        y,heavy=targets[horizon]; usable=weekly[valid_x[weekly]&np.isfinite(y[weekly])]
        issue=date_rows[usable]
        train=usable[issue+pd.to_timedelta(horizon,unit="D")<cutoff]
        test=usable[(issue>=cutoff)&(issue+pd.to_timedelta(horizon,unit="D")<test_end)]
        if len(train)<1000 or len(test)<100: raise ValueError(f"Insufficient chronological rows for {horizon}-day horizon: train={len(train)}, test={len(test)}")
        reg=new_regressor(); reg.fit(X[train],y[train]); pred=np.maximum(0,reg.predict(X[test]))
        rain_event=(y[test]>=2.5).astype(np.int8); rain_clf=new_classifier(); rain_clf.fit(X[train],(y[train]>=2.5).astype(np.int8)); rain_prob=rain_clf.predict_proba(X[test])[:,1]
        heavy_train=usable[np.isfinite(heavy[usable])]; heavy_train=heavy_train[date_rows[heavy_train]+pd.to_timedelta(horizon,unit="D")<cutoff]
        heavy_test=test[np.isfinite(heavy[test])]
        heavy_event=(heavy[heavy_test]).astype(np.int8); heavy_clf=new_classifier(); heavy_clf.fit(X[heavy_train],heavy[heavy_train].astype(np.int8)); heavy_prob=heavy_clf.predict_proba(X[heavy_test])[:,1]
        rain_climatology=seasonal_baseline(train,y[train],test,date_rows,N)
        rain_event_train=(y[train]>=2.5).astype(np.int8)
        rain_event_climatology=seasonal_baseline(train,rain_event_train,test,date_rows,N)
        recent_rate=np.maximum(0,X[test,1]*(horizon/7.0))
        report={"rainfall_total":metrics(y[test],pred,rain_event,rain_prob),
                "baselines":{"recent_7d_rate":{"mae_mm":float(mean_absolute_error(y[test],recent_rate)),"rmse_mm":float(np.sqrt(mean_squared_error(y[test],recent_rate))),"observations":int(len(test))},
                             "past_location_month_climatology":{"mae_mm":float(mean_absolute_error(y[test],rain_climatology)),"rmse_mm":float(np.sqrt(mean_squared_error(y[test],rain_climatology))),"rain_event_brier_score":float(brier_score_loss(rain_event,rain_event_climatology)),"observations":int(len(test)),"training_only":True}},
                "heavy_rain_event":{"brier_score":float(brier_score_loss(heavy_event,heavy_prob)),"event_rate":float(heavy_event.mean()),"observations":int(len(heavy_event))}}
        heavy_train_rows=usable[np.isfinite(heavy[usable])]
        heavy_train_rows=heavy_train_rows[date_rows[heavy_train_rows]+pd.to_timedelta(horizon,unit="D")<cutoff]
        heavy_test_rows=test[np.isfinite(heavy[test])]
        if len(heavy_train_rows) and len(heavy_test_rows):
            heavy_climatology=seasonal_baseline(heavy_train_rows,heavy[heavy_train_rows],heavy_test_rows,date_rows,N)
            report["heavy_rain_event"]["baseline_brier_score"]=float(brier_score_loss(heavy[heavy_test_rows].astype(np.int8),heavy_climatology))
        if len(np.unique(heavy_event))>1: report["heavy_rain_event"]["roc_auc"]=float(roc_auc_score(heavy_event,heavy_prob))
        if horizon==7:
            # Dry spell means seven consecutive future days each below 2.5 mm/day.
            dry_target=np.full((T,N),np.nan,np.float32)
            for i in range(T-7):
                vals=rain[i+1:i+8]; complete=np.isfinite(vals).all(axis=0)
                dry_target[i,complete]=np.all(vals[:,complete]<2.5,axis=0)
            dry_target=dry_target.reshape(-1)
            dry_train=usable[np.isfinite(dry_target[usable])]
            dry_train=dry_train[date_rows[dry_train]+pd.to_timedelta(7,unit="D")<cutoff]
            dry_test=test[np.isfinite(dry_target[test])]
            dry_event=dry_target[dry_test].astype(np.int8); dry_clf=new_classifier(); dry_clf.fit(X[dry_train],dry_target[dry_train].astype(np.int8)); dry_prob=dry_clf.predict_proba(X[dry_test])[:,1]
            report["dry_spell_event"]={"brier_score":float(brier_score_loss(dry_event,dry_prob)),"event_rate":float(dry_event.mean()),"observations":int(len(dry_event))}
            if len(np.unique(dry_event))>1: report["dry_spell_event"]["roc_auc"]=float(roc_auc_score(dry_event,dry_prob))
            joblib.dump(dry_clf,args.models_dir/"dry_spell_7d.joblib")
        # Fit final production artifacts only after the untouched 2024–25 chronological assessment.
        alltrain=usable
        final_reg=new_regressor(); final_reg.fit(X[alltrain],y[alltrain]); final_rain=new_classifier(); final_rain.fit(X[alltrain],(y[alltrain]>=2.5).astype(np.int8))
        final_heavy=new_classifier(); final_heavy.fit(X[alltrain],heavy[alltrain].astype(np.int8))
        joblib.dump(final_reg,args.models_dir/f"rainfall_total_{horizon}d.joblib"); joblib.dump(final_rain,args.models_dir/f"rain_probability_{horizon}d.joblib"); joblib.dump(final_heavy,args.models_dir/f"heavy_rain_probability_{horizon}d.joblib")
        model_reports[str(horizon)]={"train_rows":int(len(train)),"test_rows":int(len(test)),"test_period":[str(date_rows[test].min().date()),str(date_rows[test].max().date())],"metrics":report}
        # The only available forecast origin is the last observed day; preserve that issue date.
        latest=X[(T-1)*N:T*N]
        good=np.isfinite(latest).all(axis=1)
        rp=np.full(N,np.nan); hp=np.full(N,np.nan); yp=np.full(N,np.nan)
        if good.any(): yp[good]=np.maximum(0,final_reg.predict(latest[good])); rp[good]=final_rain.predict_proba(latest[good])[:,1]; hp[good]=final_heavy.predict_proba(latest[good])[:,1]
        dryp=np.full(N,np.nan)
        dry_model_path=args.models_dir/"dry_spell_7d.joblib"
        if horizon==7 and dry_model_path.exists(): dryp[good]=joblib.load(dry_model_path).predict_proba(latest[good])[:,1]
        forecast_date=pd.Timestamp(dates[-1]).date(); valid_start=forecast_date+timedelta(days=1); valid_end=forecast_date+timedelta(days=horizon)
        for j,location in enumerate(locations):
            outputs.append({"location_id":location["id"],"admin_unit_lgd_code":location["lgd_code"],"district_lgd_code":location["district_code"],"district_name":location["district_name"],"location_name":location["name"],"state_code":location["state_code"],"state_name":location["state_name"],"admin_level":location["admin_level"],"prediction_date":str(forecast_date),"forecast_start":str(valid_start),"forecast_end":str(valid_end),"horizon_days":horizon,"expected_rainfall_mm":None if not np.isfinite(yp[j]) else float(yp[j]),"rain_probability":None if not np.isfinite(rp[j]) else float(rp[j]),"heavy_rain_probability":None if not np.isfinite(hp[j]) else float(hp[j]),"dry_spell_probability":None if not np.isfinite(dryp[j]) else float(dryp[j]),"model_version":"mausam-imd-bihar-subdistrict-hgb-1.3","model_resolution":location["admin_level"]})
    pred_path=args.output_dir/"location_predictions.csv"
    with pred_path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(outputs[0])); writer.writeheader(); writer.writerows(outputs)
    date_end=pd.Timestamp(dates[-1]).date(); old_data=(pd.Timestamp.now(tz="UTC").date()-date_end).days>2
    occurrence_underperforms=any(v["metrics"]["rainfall_total"]["brier_score"]>v["metrics"]["baselines"]["past_location_month_climatology"]["rain_event_brier_score"] for v in model_reports.values())
    blockers=[]
    if old_data: blockers.append("The newest supplied IMD observation is older than 48 hours, so generated location outputs are archived only.")
    if occurrence_underperforms: blockers.append("Rain/no-rain Brier score exceeds the past-only location/month climatology for at least one horizon.")
    blockers.append("No archived Open-Meteo forecast runs were supplied for a forecast-versus-observation provider backtest.")
    report={"status":"research_candidate","operational":False,"operational_status":"not_ready","operational_blockers":blockers,"model_version":"mausam-imd-bihar-subdistrict-hgb-1.3","algorithm":"scikit-learn HistGradientBoosting; deterministic 3-day issue rows; district and sub-district spatial series","training_data":{"source":"user-supplied IMD daily gridded rainfall, area-weighted separately against verified LGD district and sub-district polygons","district_scope":["East Champaran (official boundary label: Purbi Champaran)","Muzaffarpur","Patna"],"date_start":str(pd.Timestamp(dates[0]).date()),"date_end":str(date_end),"raw_date_count":int(T),"district_count":3,"subdistrict_count":N-3,"location_count":N,"finite_area_weighted_location_days":int(np.isfinite(rain).sum()),"spatial_resolution_degrees":source["grid_resolution_degrees"],"source_diagnostics":source},"features":FEATURE_NAMES,"climate_index_lag":"calendar-month values shifted by one full month; source publication dates unknown","evaluation":{"method":"train issue dates ending before 2024-01-01; held-out chronological issue dates 2024-01-01 through 2025, horizon target stays inside held-out period","baselines":{"recent_7d_rate":"Latest 7-day IMD rainfall total scaled linearly to the requested horizon; issue-date features only.","past_location_month_climatology":"Mean outcome for the same location and calendar month using training rows only."},"horizons":model_reports},"provider_backtest":{"status":"unavailable","reason":"No archived provider forecast issue/valid-time pairs were found among supplied data; live Open-Meteo retrieval is not a historical forecast archive."},"prediction_records":len(outputs),"prediction_origin":str(date_end),"prediction_validity_note":"This is the newest origin supported by the supplied rainfall data. It is an archived forecast origin, not a current operational forecast; newer observations are required for current outputs.","target_definitions":{"rain_probability":"total IMD rainfall over the horizon >= 2.5 mm","heavy_rain_probability":"at least one future day >= 64.5 mm (IMD heavy-rain threshold)","dry_spell_probability":"all seven future daily IMD totals < 2.5 mm/day","expected_rainfall_mm":"predicted accumulated IMD rainfall over horizon"},"feature_sources":["IMD daily gridded rainfall","ONI monthly index","DMI monthly index"],"excluded_sources":{"ERA5/ERA5-Land":"Not used as an IMD rainfall target; model rainfall targets and spatial aggregates are derived from the supplied IMD rainfall grid."},"crop_advisory":"not generated: available 2013 calendar is reference-only/unapproved and lacks crop rainfall thresholds","onset_break":"not modeled: no complete source-supported onset/break labels"}
    (args.models_dir/"model_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"research_candidate","operational":False,"predictions":len(outputs),"districts":3,"subdistricts":N-3,"locations":N,"through":str(pd.Timestamp(dates[-1]).date()),"metrics":model_reports},indent=2))

if __name__=="__main__": main()
