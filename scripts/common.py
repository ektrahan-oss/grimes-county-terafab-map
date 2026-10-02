"""Shared pieces for the layer scripts: paths, downloads, GeoJSON output, metadata, change log."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"          # downloads; not committed
CHANGES = DATA / "changes.json"
KEEP_CHANGES = 100
MIN_FOR_SHORTFALL_CHECK = 20     # with at least this many features in a layer...
MAX_SHORTFALL = 0.3              # ...losing more than this share in one run is treated as a bad response
ALLOW_SHRINK = os.environ.get("ALLOW_SHRINK") == "1"

COUNTY_NAME = "Grimes"
COUNTY_FIPS = "48185"
WORK_CRS = 2277                  # Texas State Plane Central, US feet; used for measuring and simplifying

# Names the project in the form some public data servers require before they will answer
USER_AGENT = "Mozilla/5.0 (compatible; grimes-county-terafab-map/1.0)"
ATTEMPTS = 4
RETRY_PAUSE_SECONDS = 15


def fetch(url, params=None, timeout=120):
    """Download a URL, trying again after a pause if the server drops the connection or is overloaded."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == ATTEMPTS or not (e.code == 429 or e.code >= 500):
                raise                           # a refusal such as 403 or 404 will not get better
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == ATTEMPTS:
                raise
        print(f"  Download failed, trying again in {RETRY_PAUSE_SECONDS * attempt} seconds ({attempt} of {ATTEMPTS - 1})", flush=True)
        time.sleep(RETRY_PAUSE_SECONDS * attempt)


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


def record_layer(layer_id, source, feature_count):
    """Note in data/meta.json when a layer was last built, from where, and how many features it has."""
    path = DATA / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    today = date.today().isoformat()
    meta["updated"] = today
    meta.setdefault("layers", {})[layer_id] = {"updated": today, "source": source, "feature_count": feature_count}
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# ---- Change log ----

def load_changes():
    """Entries in data/changes.json, oldest first, all in the {date, layer, summary} shape."""
    if not CHANGES.exists():
        return []
    entries = json.loads(CHANGES.read_text(encoding="utf-8"))
    # build_reinvestment_zone.py writes its own shape; bring those entries into line
    return [e if "summary" in e else {"date": e["date"], "layer": "zone", "summary": e["change"]} for e in entries]


def save_changes(entries):
    CHANGES.write_text(json.dumps(entries[-KEEP_CHANGES:], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def log_change(layer_id, summary, link=None):
    entry = {"date": date.today().isoformat(), "layer": layer_id, "summary": summary}
    if link:
        entry["link"] = link
    save_changes(load_changes() + [entry])


def _fingerprint(item):
    return hashlib.sha1(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _count(n, noun):
    return f"{n:,} {noun[0] if n == 1 else noun[1]}"


def describe_change(old, new, noun, key=None):
    """Plain-language difference between two lists of items, or None if they match.

    With a key (a function giving each item's identity), items are added, removed or changed.
    Without one, any edit to an item shows up as one removed and one added.
    """
    if old is None:
        return f"new, with {_count(len(new), noun)}"
    if key:
        before, after = {key(i): _fingerprint(i) for i in old}, {key(i): _fingerprint(i) for i in new}
        added, removed = len(after.keys() - before.keys()), len(before.keys() - after.keys())
        changed = sum(1 for k in after.keys() & before.keys() if after[k] != before[k])
    else:
        before, after = Counter(map(_fingerprint, old)), Counter(map(_fingerprint, new))
        added, removed, changed = sum((after - before).values()), sum((before - after).values()), 0
    parts = [f"{_count(added, noun)} added" if added else "",
             f"{_count(removed, noun)} removed" if removed else "",
             f"{_count(changed, noun)} changed" if changed else ""]
    return ", ".join(p for p in parts if p) or None


def report_change(layer_id, label, summary, link=None):
    if summary:
        log_change(layer_id, f"{label}: {summary}", link)
        print(f"  Change logged: {label}: {summary}")
    else:
        print("  No change since the last run.")


# ---- Layer output ----

def publish_features(layer_id, label, path, features, source, noun=("feature", "features"), key=None):
    """Write GeoJSON features, log what changed since the last version, and update meta.json.

    key names a property that identifies each feature across runs. Returns the file size in bytes.
    """
    old = json.loads(path.read_text(encoding="utf-8"))["features"] if path.exists() else None
    if old and len(old) >= MIN_FOR_SHORTFALL_CHECK and len(features) < len(old) * (1 - MAX_SHORTFALL) and not ALLOW_SHRINK:
        # A source that is down or mid-update can return a fraction of its data. Keep the file we have.
        raise SystemExit(f"{label}: the source returned {len(features):,} {noun[1]} where the file has {len(old):,}. "
                         "That looks incomplete, so nothing was written. If the drop is real, run again with "
                         "ALLOW_SHRINK=1 set in the environment.")
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False, separators=(", ", ": ")) for f in features)
    path.write_text('{\n  "type": "FeatureCollection",\n  "features": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")
    ident = (lambda f: f["properties"].get(key)) if key else None
    report_change(layer_id, label, describe_change(old, features, noun, ident))
    record_layer(layer_id, source, len(features))
    return path.stat().st_size


def publish_geojson(layer_id, label, path, gdf, source, noun=("feature", "features"), key=None):
    """publish_features for a GeoDataFrame: reprojects to EPSG:4326, rounds coordinates, drops empty properties."""
    features = json.loads(gdf.to_crs(4326).to_json(drop_id=True))["features"]
    for f in features:
        f["geometry"]["coordinates"] = _rounded(f["geometry"]["coordinates"])
        f["properties"] = {k: v for k, v in f["properties"].items() if v is not None}
    return publish_features(layer_id, label, path, features, source, noun, key)


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
