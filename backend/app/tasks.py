"""Scheduled, provenance-preserving jobs. They never create fabricated values."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from celery import Celery
import httpx

from .config import settings
from .db_connection import connect_database
from .providers import OpenMeteoSeasonalProvider

celery_app = Celery("mausam", broker=settings.redis_url or "redis://127.0.0.1:6379/0")
celery_app.conf.update(
    timezone="UTC",
    beat_schedule={
        "ingest-ec46-daily": {"task":"backend.app.tasks.ingest_ec46_daily","schedule":86400.0},
        "ingest-public-climate-daily": {"task":"backend.app.tasks.ingest_public_climate_indices","schedule":86400.0},
        "queue-approved-alerts-hourly": {"task":"backend.app.tasks.queue_approved_alerts","schedule":3600.0},
        "dispatch-alerts-every-five-minutes": {"task":"backend.app.tasks.dispatch_notifications","schedule":300.0},
    },
)


@celery_app.task(name="backend.app.tasks.ingest_ec46_daily")
def ingest_ec46_daily():
    return asyncio.run(_ingest_ec46_daily())


async def _ingest_ec46_daily():
    if not settings.enable_open_meteo_ingestion:
        return {"status":"disabled","reason":"ENABLE_OPEN_METEO_INGESTION is false."}
    if not settings.database_url:
        return {"status":"unavailable","reason":"DATABASE_URL is missing."}
    db = await connect_database(settings.database_url)
    provider = OpenMeteoSeasonalProvider(settings.open_meteo_customer_url or settings.open_meteo_seasonal_url)
    inserted = 0
    failed = 0
    try:
        # District-level fetch only until a meteorological review accepts finer-scale use.
        rows = await db.fetch(
            """SELECT id, extensions.ST_Y(extensions.ST_PointOnSurface(boundary)) AS lat, extensions.ST_X(extensions.ST_PointOnSurface(boundary)) AS lon
               FROM locations WHERE level='district' AND active=true AND boundary IS NOT NULL"""
        )
        for row in rows:
            try:
                payload = await provider.forecast(float(row["lat"]), float(row["lon"]))
                stamp = datetime.now(timezone.utc)
                await db.execute(
                    """INSERT INTO provider_forecasts(location_id,provider,model_name,issue_at,retrieved_at,payload)
                       VALUES($1,$2,'ecmwf_ec46',NULL,$3,$4::jsonb)""",
                    row["id"], "Open-Meteo / ECMWF", stamp, json.dumps(payload),
                )
                inserted += 1
            except Exception:
                # Do not log provider response bodies or any personal data.
                failed += 1
        return {"status":"complete","fetched":inserted,"failed":failed,"retrieved_at":datetime.now(timezone.utc).isoformat()}
    finally:
        await db.close()


@celery_app.task(name="backend.app.tasks.ingest_public_climate_indices")
def ingest_public_climate_indices():
    return asyncio.run(_ingest_public_climate_indices())


async def _ingest_public_climate_indices():
    """Import published NOAA ONI/DMI and BOM RMM series with their source links."""
    if not settings.enable_public_climate_ingestion:
        return {"status":"disabled","reason":"ENABLE_PUBLIC_CLIMATE_INGESTION is false."}
    if not settings.database_url:
        return {"status":"unavailable","reason":"DATABASE_URL is missing."}
    sources = {
        "noaa_oni": ("NOAA Climate Prediction Center", "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", "ONI"),
        "noaa_dmi": ("NOAA Physical Sciences Laboratory / HadISST", "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.csv", "IOD/DMI"),
        "bom_rmm": ("Australian Bureau of Meteorology", "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt", "MJO/RMM"),
    }
    stamp=datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=45, follow_redirects=True, headers={"User-Agent":"MausamClimateData/1.0 (source-attributed research ingestion)"}) as client:
        responses={key: await client.get(url) for key,(_,url,_) in sources.items()}
    for response in responses.values():
        response.raise_for_status()
    rows=[]
    # CPC reports ONI as overlapping seasons (DJF...NDJ); preserve their published value and label.
    for line in responses["noaa_oni"].text.splitlines():
        cols=line.split()
        if len(cols)<13 or not cols[0].isdigit() or len(cols[0])!=4: continue
        year=int(cols[0])
        for season,value in zip(("DJF","JFM","FMA","MAM","AMJ","MJJ","JJA","JAS","ASO","SON","OND","NDJ"),cols[1:13]):
            try: val=float(value)
            except ValueError: continue
            # Use season middle month as the index date; retain season in the phase field as absent and details below.
            end_month={"DJF":2,"JFM":3,"FMA":4,"MAM":5,"AMJ":6,"MJJ":7,"JJA":8,"JAS":9,"ASO":10,"SON":11,"OND":12,"NDJ":1}[season]
            date=f"{year+1 if season=='NDJ' else year:04d}-{end_month:02d}-01"
            rows.append(("ONI",date,val,None,None,None,stamp,{"season":season,"url":sources["noaa_oni"][1]}))
    # NOAA PSL CSV uses monthly time/value columns; accept either year/month columns or ISO date plus value.
    for line in responses["noaa_dmi"].text.splitlines():
        cols=[c.strip() for c in line.split(",")]
        if len(cols)<2: continue
        try:
            nums=[float(c) for c in cols if c]
        except ValueError: continue
        if len(nums)>=3 and 1800<=nums[0]<=2200 and 1<=nums[1]<=12:
            date=f"{int(nums[0]):04d}-{int(nums[1]):02d}-01";value=nums[2]
        elif len(nums)>=2 and 1800<=nums[0]<=2200:
            # PSL CSV may encode date as decimal year.
            year=int(nums[0]);month=max(1,min(12,int(round((nums[0]-year)*12))+1));date=f"{year:04d}-{month:02d}-01";value=nums[1]
        else: continue
        if abs(value)>10: continue
        rows.append(("DMI",date,value,None,None,None,stamp,{"url":sources["noaa_dmi"][1]}))
    # BOM RMM daily whitespace series: year month day RMM1 RMM2 phase amplitude.
    for line in responses["bom_rmm"].text.splitlines():
        cols=line.split()
        if len(cols)<7: continue
        try: year,month,day=int(cols[0]),int(cols[1]),int(cols[2]);rmm1,rmm2,phase,amp=map(float,cols[3:7])
        except ValueError: continue
        if year<1974 or rmm1>1000 or rmm2>1000 or amp>1000 or not 1<=phase<=8: continue
        rows.append(("RMM",f"{year:04d}-{month:02d}-{day:02d}",rmm1,rmm2,int(phase),amp,stamp,{"url":sources["bom_rmm"][1]}))
    db=await connect_database(settings.database_url)
    try:
        async with db.transaction():
            for source_id,(name,url,dataset) in sources.items():
                await db.execute("""INSERT INTO data_sources(id,source_name,source_url,dataset_name,licence,configured,last_retrieved_at,last_status,details)
                    VALUES($1,$2,$3,$4,'Public source; see provider terms',true,$5,'available',$6::jsonb)
                    ON CONFLICT(id) DO UPDATE SET last_retrieved_at=$5,last_status='available',configured=true""",
                    source_id,name,url,dataset,stamp,json.dumps({"parser":"public-series-v1"}))
            for index,date,v1,v2,phase,amplitude,retrieved,meta in rows:
                key="noaa_oni" if index=="ONI" else "noaa_dmi" if index=="DMI" else "bom_rmm"
                await db.execute("""INSERT INTO climate_indices(index_name,valid_at,value_1,value_2,phase,amplitude,details,source_id,retrieved_at)
                    VALUES($1,$2::date,$3,$4,$5,$6,$7::jsonb,$8,$9)
                    ON CONFLICT(index_name,valid_at,source_id) DO UPDATE SET value_1=$3,value_2=$4,phase=$5,amplitude=$6,details=$7::jsonb,retrieved_at=$9""",
                    index,date,v1,v2,phase,amplitude,json.dumps(meta),key,retrieved)
            for source_id, response in responses.items():
                await db.execute("UPDATE data_sources SET last_retrieved_at=$2,last_status='available',details=details || $3::jsonb WHERE id=$1",
                    source_id,stamp,json.dumps({"http_status":response.status_code}))
        return {"status":"complete","records_upserted":len(rows),"retrieved_at":stamp.isoformat()}
    finally:
        await db.close()


@celery_app.task(name="backend.app.tasks.queue_approved_alerts")
def queue_approved_alerts():
    return asyncio.run(_queue_approved_alerts())


async def _queue_approved_alerts():
    if not settings.database_url:
        return {"status":"unavailable","reason":"DATABASE_URL is missing."}
    db=await connect_database(settings.database_url)
    try:
        rows=await db.fetch(
            """SELECT a.id AS advisory_id,a.farmer_id,a.kind,np.sms_enabled,np.whatsapp_enabled,
                      np.daily_weather,np.severe_weather,np.crop_advisory,np.sowing_advisory
               FROM advisories a JOIN farmer_profiles fp ON fp.id=a.farmer_id
               JOIN notification_preferences np ON np.farmer_id=fp.id
               WHERE a.approved=true AND a.valid_from<=now() AND a.valid_until>=now()
                 AND ((np.sms_enabled OR np.whatsapp_enabled)
                   AND CASE a.kind WHEN 'daily_weather' THEN np.daily_weather
                     WHEN 'severe_weather' THEN np.severe_weather
                     WHEN 'sowing_advisory' THEN np.sowing_advisory
                     ELSE np.crop_advisory END)
                 AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.advisory_id=a.id AND n.farmer_id=a.farmer_id)"""
        )
        for row in rows:
            channel="sms" if row["sms_enabled"] else "whatsapp"
            await db.execute("INSERT INTO notifications(id,farmer_id,advisory_id,channel,status) VALUES($1,$2,$3,$4,'queued')",
                             uuid4(),row["farmer_id"],row["advisory_id"],channel)
        return {"status":"queued","count":len(rows)}
    finally:
        await db.close()


@celery_app.task(name="backend.app.tasks.dispatch_notifications")
def dispatch_notifications():
    return asyncio.run(_dispatch_notifications())


async def _dispatch_notifications():
    """Deliver approved queued alerts only. Missing credentials yield blocked, never sent."""
    if not settings.enable_notification_dispatch:
        return {"status":"disabled","reason":"ENABLE_NOTIFICATION_DISPATCH is false."}
    if not settings.database_url:
        return {"status":"unavailable","reason":"DATABASE_URL is missing."}
    db=await connect_database(settings.database_url)
    sent=failed=blocked=0
    try:
        rows=await db.fetch(
            """SELECT n.id,n.channel,n.provider_message_id,a.body,fp.language,au.phone
               FROM notifications n JOIN advisories a ON a.id=n.advisory_id
               JOIN farmer_profiles fp ON fp.id=n.farmer_id
               JOIN auth.users au ON au.id=fp.auth_user_id
               WHERE n.status='queued' AND a.approved=true AND a.valid_until>=now() ORDER BY n.created_at LIMIT 100"""
        )
        for row in rows:
            try:
                if row["channel"]=="sms" and settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_messaging_service_sid:
                    async with httpx.AsyncClient(timeout=15,auth=(settings.twilio_account_sid,settings.twilio_auth_token)) as client:
                        response=await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                            data={"To":row["phone"],"MessagingServiceSid":settings.twilio_messaging_service_sid,"Body":row["body"]})
                    if response.is_success:
                        await db.execute("UPDATE notifications SET status='sent',provider='Twilio',provider_message_id=$2,sent_at=now() WHERE id=$1",row["id"],response.json().get("sid"))
                        sent+=1
                    else:
                        await db.execute("UPDATE notifications SET status='failed',provider='Twilio',error_code=$2 WHERE id=$1",row["id"],str(response.status_code))
                        failed+=1
                else:
                    await db.execute("UPDATE notifications SET status='blocked',error_code='provider_not_configured' WHERE id=$1",row["id"])
                    blocked+=1
            except Exception:
                await db.execute("UPDATE notifications SET status='failed',error_code='delivery_error' WHERE id=$1",row["id"])
                failed+=1
        return {"status":"complete","sent":sent,"failed":failed,"blocked":blocked}
    finally:
        await db.close()
