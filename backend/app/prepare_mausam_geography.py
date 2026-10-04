"""Filter supplied national TopoJSON files into MAUSAM's three Bihar districts.

The input files are never modified. TopoJSON is decoded to GeoJSON while
preserving source properties and geometry. The selected layers are Bihar,
districts, blocks, and sub-districts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TARGETS = {"purbi champaran": ("East Champaran", "213"), "muzaffarpur": ("Muzaffarpur", "208"), "patna": ("Patna", "212")}
FILES = {"state": "states", "district": "districts", "block": "blocks", "subdistrict": "subdistricts"}


def decode_topology(topology: dict[str, Any], object_name: str) -> list[dict[str, Any]]:
    transform = topology.get("transform")
    arcs = []
    for encoded in topology["arcs"]:
        points, x, y = [], 0, 0
        for dx, dy in encoded:
            x += dx
            y += dy
            if transform:
                sx, sy = transform["scale"]
                tx, ty = transform["translate"]
                points.append([x * sx + tx, y * sy + ty])
            else:
                points.append([x, y])
        arcs.append(points)

    def stitch(indices: list[int]) -> list[list[float]]:
        ring: list[list[float]] = []
        for index in indices:
            segment = arcs[~index] if index < 0 else arcs[index]
            if index < 0:
                segment = list(reversed(segment))
            ring.extend(segment if not ring else segment[1:])
        return ring

    def convert(geometry: dict[str, Any]) -> dict[str, Any]:
        kind, refs = geometry["type"], geometry.get("arcs")
        if kind == "Polygon":
            coords = [stitch(ring) for ring in refs]
        elif kind == "MultiPolygon":
            coords = [[stitch(ring) for ring in polygon] for polygon in refs]
        else:
            raise ValueError(f"Unsupported geometry type {kind} in {object_name}")
        return {"type": kind, "coordinates": coords}

    return [{"type": "Feature", "id": g.get("id"), "geometry": convert(g), "properties": dict(g["properties"])}
            for g in topology["objects"][object_name]["geometries"]]


def write_layer(source: Path, output: Path, file_key: str, level: str) -> list[dict[str, Any]]:
    topo = json.loads(source.read_text(encoding="utf-8"))
    features = decode_topology(topo, file_key)
    if level == "state":
        selected = [f for f in features if f["properties"].get("name", "").casefold() == "bihar"]
        for f in selected:
            p = f["properties"]
            p.update(id="IN-BR-S-10", name="Bihar", level="state", parent_id=None,
                     mausam_level="state", source_code=str(p.get("lgd")), parent_code=None)
    elif level == "district":
        selected = [f for f in features if f["properties"].get("state_lgd") == 10
                    and str(f["properties"].get("lgd")) in {v[1] for v in TARGETS.values()}]
        for f in selected:
            p = f["properties"]
            display, _ = TARGETS[p["name"].casefold()]
            code = str(p["lgd"])
            p.update(id=f"IN-BR-D-{code}", name=display, source_name=p["name"], level="district",
                     parent_id="IN-BR-S-10", mausam_level="district", source_code=code,
                     parent_code="10", display_name=display, district_code=code, state_code="10")
    else:
        wanted_codes = {v[1] for v in TARGETS.values()}
        selected = [f for f in features if f["properties"].get("state_lgd") == 10
                    and str(f["properties"].get("dist_lgd")) in wanted_codes]
        for f in selected:
            p = f["properties"]
            code, district_code = str(p["lgd"]), str(p["dist_lgd"])
            level_name = "block" if level == "block" else "subdistrict"
            p.update(id=f"IN-BR-{level_name[:1].upper()}-{code}", source_name=p["name"],
                     level=level_name, mausam_level=level_name, source_code=code,
                     parent_code=district_code, parent_id=f"IN-BR-D-{district_code}",
                     district_code=district_code, state_code="10")
            p["block_code" if level_name == "block" else "subdistrict_code"] = code
    if not selected:
        raise ValueError(f"No features selected for {level}; inspect source attributes and LGD codes")
    collection = {"type": "FeatureCollection", "name": f"MAUSAM Bihar {level} boundaries", "features": selected}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return selected


def prepare(source_dir: Path, output_dir: Path) -> dict[str, int]:
    counts = {}
    ordered = []
    for level, stem in FILES.items():
        features = write_layer(source_dir / f"{stem}.topo.json", output_dir / f"{level}s.geojson", stem, level)
        counts[level] = len(features)
        ordered.extend(features)
    combined = {"type": "FeatureCollection", "name": "MAUSAM Bihar operational geography", "features": ordered}
    (output_dir / "locations.geojson").write_text(json.dumps(combined, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return counts


def validate(directory: Path) -> dict[str, int]:
    counts = {}
    districts = json.loads((directory / "districts.geojson").read_text())["features"]
    expected = {"213", "208", "212"}
    assert {str(f["properties"]["lgd"]) for f in districts} == expected, "District scope contamination/missing district"
    district_by_code = {str(f["properties"]["lgd"]): f for f in districts}
    assert all(f["properties"].get("state") == "Bihar" for f in districts), "Non-Bihar district found"
    assert len({str(f["properties"]["lgd"]) for f in districts}) == len(districts), "Duplicate district code"
    for feature in districts:
        assert feature["geometry"].get("type") in {"Polygon", "MultiPolygon"}, "District geometry is not polygonal"
    for layer in ("blocks", "subdistricts"):
        features = json.loads((directory / f"{layer}.geojson").read_text())["features"]
        assert all(f["properties"].get("state") == "Bihar" and str(f["properties"].get("dist_lgd")) in expected for f in features), f"Invalid parent in {layer}"
        counts[layer] = len(features)
        for code in expected:
            children = [f for f in features if str(f["properties"].get("dist_lgd")) == code]
            if not children:
                raise AssertionError(f"No {layer} features for district LGD {code}")
            assert all(f["properties"].get("parent_id") == district_by_code[code]["properties"]["id"] for f in children), f"Incorrect {layer} parent mapping for district {code}"
        for feature in features:
            geometry = feature["geometry"]
            assert geometry and geometry.get("type") in {"Polygon", "MultiPolygon"}, f"Missing actual geometry in {layer}"
            assert str(feature["properties"].get("lgd")) and str(feature["properties"].get("dist_lgd")) in district_by_code, f"Missing/invalid administrative code in {layer}"
    counts["district"] = len(districts)
    counts["state"] = len(json.loads((directory / "states.geojson").read_text())["features"])
    if counts["state"] != 1:
        raise AssertionError("Expected exactly Bihar as the only state feature")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("backend/data/geography/mausam/bihar"))
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    counts = prepare(args.source_dir, args.output)
    counts = validate(args.output)
    print(json.dumps({"counts": counts, "validation": "passed"}, indent=2))
