"""Extract Bihar district features from NWIC's national district GeoJSON.

Coordinates remain in the source CRS. Use load_boundaries with --source-srid 7755
so PostGIS transforms them to WGS84 before storing.
"""
import argparse
import json
import mmap
from pathlib import Path


def extract(source: Path, output: Path) -> int:
    with source.open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            marker = data.find(b'"features"')
            if marker < 0:
                raise ValueError("GeoJSON has no features array")
            i = data.find(b"[", marker)
            if i < 0:
                raise ValueError("GeoJSON features value is not an array")
            i += 1
            features = []
            while i < len(data):
                while i < len(data) and data[i] in b" \t\r\n,":
                    i += 1
                if i >= len(data) or data[i] == ord("]"):
                    break
                if data[i] != ord("{"):
                    raise ValueError(f"Unexpected token at byte {i} in features array")
                start = i
                depth = 0
                in_string = False
                escaped = False
                while i < len(data):
                    byte = data[i]
                    if in_string:
                        if escaped:
                            escaped = False
                        elif byte == ord("\\"):
                            escaped = True
                        elif byte == ord('"'):
                            in_string = False
                    elif byte == ord('"'):
                        in_string = True
                    elif byte == ord("{"):
                        depth += 1
                    elif byte == ord("}"):
                        depth -= 1
                        if depth == 0:
                            i += 1
                            feature = json.loads(data[start:i])
                            props = feature.get("properties") or {}
                            if props.get("state_name", "").strip().casefold() == "bihar":
                                code = str(props.get("dtcode") or props.get("id"))
                                name = str(props.get("district", "")).strip()
                                if not name or not code:
                                    raise ValueError("A Bihar district is missing its name or district code")
                                feature["properties"] = {
                                    "id": f"IN-BR-{code}",
                                    "name": name,
                                    "level": "district",
                                    "parent_id": None,
                                    "state_name": "Bihar",
                                    "source_code": code,
                                    "source_agency": "Survey of India (SOI), as attributed in supplied NWIC GeoJSON",
                                }
                                features.append(feature)
                            break
                    i += 1
                else:
                    raise ValueError("Unterminated feature object")
    if not features:
        raise ValueError("No Bihar district features found")
    # Copy only CRS metadata from source; the loader receives its numeric EPSG via CLI.
    collection = {"type": "FeatureCollection", "name": "Bihar districts (NWIC)", "features": features}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(features)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("backend/data/bihar_districts_nwic.geojson"))
    args = parser.parse_args()
    print(f"Wrote {extract(args.source, args.output)} Bihar district features to {args.output}")
