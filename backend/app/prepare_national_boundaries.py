"""Convert supplied LGD TopoJSON into indexed, source-preserving GeoJSON.

The bundled Maps downloads are TopoJSON with an explicit longitude/latitude
quantization transform. This converts arcs without altering source coordinates.
"""
from __future__ import annotations
import gzip, json
from pathlib import Path
from typing import Any
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/"data/raw/administrative_boundaries/maps"
OUT=ROOT/"data/processed/administrative"

def convert(name:str, level:str)->dict[str,Any]:
    source=RAW/f"{name}.topo.json"
    if not source.is_file(): raise FileNotFoundError(f"Required supplied boundary file not found: {source}")
    topo=json.loads(source.read_text(encoding="utf-8")); transform=topo.get("transform")
    if not transform or not transform.get("scale") or not transform.get("translate"):
        raise ValueError(f"{source}: TopoJSON coordinate transform is missing")
    sx,sy=transform["scale"]; tx,ty=transform["translate"]
    decoded=[]
    for arc in topo["arcs"]:
        x=y=0; coords=[]
        for dx,dy in arc:
            x+=dx; y+=dy; coords.append([x*sx+tx,y*sy+ty])
        decoded.append(coords)
    def arc_coords(ref:int):
        coords=decoded[ref if ref>=0 else ~ref]
        return coords if ref>=0 else list(reversed(coords))
    def ring(refs):
        result=[]
        for ref in refs:
            part=arc_coords(ref)
            result.extend(part if not result else part[1:])
        if result and result[0]!=result[-1]: result.append(result[0])
        return result
    def geom(g):
        kind=g["type"]; arcs=g.get("arcs")
        if kind=="Polygon": return {"type":"Polygon","coordinates":[ring(r) for r in arcs]}
        if kind=="MultiPolygon": return {"type":"MultiPolygon","coordinates":[[ring(r) for r in poly] for poly in arcs]}
        if kind=="GeometryCollection": return {"type":kind,"geometries":[geom(x) for x in g["geometries"]]}
        raise ValueError(f"Unsupported TopoJSON geometry type {kind!r} in {source}")
    obj=next(iter(topo["objects"].values()))
    shapes=obj.get("geometries",[])
    output=[]; by_unit={}
    for g in shapes:
        p=dict(g.get("properties") or {})
        lgd=str(p.get("lgd") or p.get("id") or "").strip()
        state=str(p.get("state_lgd") or "").strip()
        district=str(p.get("dist_lgd") or "").strip()
        if not lgd: raise ValueError(f"{source}: missing official LGD id")
        if level=="district": unit_id=f"IN-LGD-D-{state}-{lgd}"; parent=f"IN-LGD-ST-{state}"; dist_code=lgd; dist_name=p.get("name")
        elif level=="block": unit_id=f"IN-LGD-B-{state}-{district}-{lgd}"; parent=f"IN-LGD-D-{state}-{district}"; dist_code=district; dist_name=p.get("district")
        elif level=="subdistrict": unit_id=f"IN-LGD-S-{state}-{district}-{lgd}"; parent=f"IN-LGD-D-{state}-{district}"; dist_code=district; dist_name=p.get("district")
        else: raise ValueError(level)
        props={**p,"id":unit_id,"level":level,"mausam_level":level,"lgd_code":lgd,"state_code":state,"district_code":dist_code,
               "district_name":dist_name,"parent_id":parent,"source_file":source.name}
        if level=="block": props["block_code"]=lgd
        if level=="subdistrict": props["subdistrict_code"]=lgd
        feature={"type":"Feature","id":unit_id,"properties":props,"geometry":geom(g)}
        if unit_id in by_unit:
            current=by_unit[unit_id]
            current["geometry"]=mapping(unary_union([shape(current["geometry"]),shape(feature["geometry"])]))
        else:
            by_unit[unit_id]=feature
    output=list(by_unit.values()); ids={f["properties"]["lgd_code"] for f in output}
    collection={"type":"FeatureCollection","name":f"India LGD {level} boundaries","crs":{"type":"name","properties":{"name":"OGC:CRS84"}},"features":output}
    target=OUT/f"{level}s.geojson.gz"; target.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(target,"wt",encoding="utf-8",compresslevel=6) as f: json.dump(collection,f,separators=(",",":"),ensure_ascii=False)
    return {"level":level,"source":str(source.relative_to(ROOT)),"output":str(target.relative_to(ROOT)),"features":len(output),"unique_lgd_ids":len(ids),"crs":"OGC:CRS84 (decoded from supplied TopoJSON transform)"}

def main():
    results=[convert("districts","district"),convert("blocks","block"),convert("subdistricts","subdistrict")]
    index=[]
    for level,result in zip(("district","block","subdistrict"),results):
        with gzip.open(OUT/f"{level}s.geojson.gz","rt",encoding="utf-8") as f: coll=json.load(f)
        for feature in coll["features"]:
            p=feature["properties"]
            index.append({"id":p["id"],"name":p.get("name"),"level":level,"parent_id":p.get("parent_id"),
                          "state_code":str(p.get("state_code") or ""),"state_name":p.get("state") or "",
                          "district_code":str(p.get("district_code") or ""),"district_name":p.get("district_name") or p.get("name") or "",
                          "block_code":str(p.get("block_code") or ""),"subdistrict_code":str(p.get("subdistrict_code") or ""),"source_code":str(p.get("lgd_code") or "")})
    (OUT/"locations.json").write_text(json.dumps({"source":"user-provided Maps TopoJSON + embedded official LGD identifiers","records":len(index),"locations":index},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    (OUT/"boundary_report.json").write_text(json.dumps({"status":"processed","levels":results,"location_index_count":len(index)},indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"processed","levels":results,"location_index_count":len(index)},indent=2))

if __name__=="__main__": main()
