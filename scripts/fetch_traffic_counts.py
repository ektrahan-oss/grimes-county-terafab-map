"""Pull TxDOT annual traffic counts for Grimes County into data/traffic_counts.geojson.

Source: TxDOT Annual Average Daily Traffic Counts (Public), an ArcGIS feature
service of count stations. Each station carries the latest AADT plus up to 19
prior years.

Run from anywhere:  python scripts/fetch_traffic_counts.py
Needs Python 3.8+ and nothing else.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

SERVICE = (
    "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/"
    "TxDOT_AADT_Annuals_(Public_View)/FeatureServer/0/query"
)
COUNTY = "Grimes"
YEARS = 5        # how many years of counts to keep per station, newest first
PAGE_SIZE = 1000

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "traffic_counts.geojson"
META = DATA / "meta.json"

# TxDOT road prefixes, spelled the way people say them
PREFIXES = {"IH": "I-", "US": "US ", "SH": "SH ", "SL": "Loop ", "SS": "Spur ",
            "FM": "FM ", "RM": "RM ", "BS": "Bus. SH ", "BU": "Bus. US ",
            "BF": "Bus. FM ", "TL": "Toll ", "PR": "Park Road ", "CR": "CR "}


def fetch_page(offset):
    params = {
        "where": f"UPPER(CNTY_NM) = '{COUNTY.upper()}'",
        "outFields": "*",
        "outSR": 4326,
        "orderByFields": "OBJECTID_1",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "f": "geojson",
    }
    url = SERVICE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        page = json.load(r)
    # ArcGIS reports errors in the body with a 200 status
    if "error" in page:
        raise RuntimeError(f"TxDOT service error: {page['error']}")
    return page


def fetch_all():
    features, offset = [], 0
    while True:
        page = fetch_page(offset)
        features += page.get("features", [])
        if not page.get("properties", {}).get("exceededTransferLimit"):
            return features
        offset += PAGE_SIZE


def road_name(code):
    """'SH0030' -> 'SH 30', 'FM1774' -> 'FM 1774'. Unknown shapes pass through."""
    m = re.fullmatch(r"([A-Z]{2})0*(\d+)([A-Z]?)", (code or "").strip())
    if not m or m.group(1) not in PREFIXES:
        return (code or "").strip() or "Traffic count station"
    return PREFIXES[m.group(1)] + m.group(2) + m.group(3)


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def simplify(feature):
    """Reduce a TxDOT record to what the map popup shows."""
    p = feature["properties"]
    year = p["AADT_RPT_YEAR"]
    props = {
        "name": road_name(p.get("ON_ROAD")),
        "description": "Average vehicles per day, both directions",
        "Station": p.get("TRFC_STATN_ID"),
    }
    for back in range(YEARS):
        field = "AADT_RPT_QTY" if back == 0 else f"AADT_RPT_HIST_{back:02d}_QTY"
        if p.get(field) is not None:
            props[f"AADT {year - back}"] = p[field]
    lng, lat = feature["geometry"]["coordinates"][:2]
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
    }


def main():
    raw = fetch_all()
    stations = [f for f in raw if f.get("geometry") and f["properties"].get("AADT_RPT_YEAR")]
    if not stations:
        sys.exit(f"No traffic count stations returned for {COUNTY} County. Nothing written.")

    features = sorted((simplify(f) for f in stations),
                      key=lambda f: natural(f["properties"]["Station"] or ""))
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False) for f in features)
    OUT.write_text('{\n  "type": "FeatureCollection",\n  "features": [\n' + lines + "\n  ]\n}\n",
                   encoding="utf-8")

    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    meta["updated"] = date.today().isoformat()
    META.write_text(json.dumps(meta) + "\n", encoding="utf-8")

    year = max(f["properties"]["AADT_RPT_YEAR"] for f in stations)
    print(f"Wrote {len(features)} stations to {OUT} (latest count year {year}).")


if __name__ == "__main__":
    main()
