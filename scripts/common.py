"""Shared pieces for the layer scripts: paths, downloads, GeoJSON output, metadata."""

import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"          # downloads; not committed

COUNTY_NAME = "Grimes"
COUNTY_FIPS = "48185"
WORK_CRS = 2277                  # Texas State Plane Central, US feet; used for measuring and simplifying

# Names the project in the form some public data servers require before they will answer
USER_AGENT = "Mozilla/5.0 (compatible; grimes-county-terafab-map/1.0)"


def fetch(url, params=None, timeout=120):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url, params=None, timeout=120):
    data = json.loads(fetch(url, params, timeout))
    # ArcGIS reports errors in the body with a 200 status
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"{url} returned an error: {data['error']}")
    return data


def _rounded(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [_rounded(c) for c in coords]


def write_geojson(path, gdf):
    """Write a GeoDataFrame as compact EPSG:4326 GeoJSON, one feature per line. Returns size in bytes."""
    features = json.loads(gdf.to_crs(4326).to_json(drop_id=True))["features"]
    for f in features:
        f["geometry"]["coordinates"] = _rounded(f["geometry"]["coordinates"])
        f["properties"] = {k: v for k, v in f["properties"].items() if v is not None}
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False, separators=(", ", ": ")) for f in features)
    path.write_text('{\n  "type": "FeatureCollection",\n  "features": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")
    return path.stat().st_size


def record_layer(layer_id, source, feature_count):
    """Note in data/meta.json when a layer was last built, from where, and how many features it has."""
    path = DATA / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    today = date.today().isoformat()
    meta["updated"] = today
    meta.setdefault("layers", {})[layer_id] = {"updated": today, "source": source, "feature_count": feature_count}
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def county_boundary(buffer_miles=0):
    """Grimes County as a one-row GeoDataFrame in EPSG:4326, optionally grown by a buffer."""
    import geopandas as gpd

    path = DATA / "grimes_county.geojson"
    if not path.exists():
        raise SystemExit("data/grimes_county.geojson is missing. Run scripts/fetch_county_boundary.py first.")
    county = gpd.read_file(path)[["geometry"]]
    if buffer_miles:
        county = county.to_crs(WORK_CRS)
        county["geometry"] = county.buffer(buffer_miles * 5280)
        county = county.to_crs(4326)
    return county
