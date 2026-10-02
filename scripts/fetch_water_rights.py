"""Pull surface water rights near Grimes County into data/water_rights.geojson.

Source: Texas Commission on Environmental Quality (TCEQ), Water Rights Viewer. A surface water
right lets its holder take or store water from a river, creek or reservoir. Each point is a place
named in a right: where water is diverted, stored, released or discharged. Covers the county plus
a 5 mile buffer.

Holders' names are deliberately left out: many small rights belong to private individuals.

    python scripts/fetch_water_rights.py
"""

import sys

import geopandas as gpd

from common import DATA, county_boundary, fetch_json, publish_geojson

SERVICE = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer"
POINTS = f"{SERVICE}/3/query"    # "Water Rights As Single Points"
USE = f"{SERVICE}/13/query"      # "Water Use": reported use and yearly diversions, one row per right, use and year
BUFFER_MILES = 5
GALLONS_PER_ACRE_FOOT = 325851   # an acre of water one foot deep
OUT = DATA / "water_rights.geojson"

TYPES = {
    "D/S Limit - Diversion Segment": "Downstream end of a diversion reach",
    "U/S Limit - Diversion Segment": "Upstream end of a diversion reach",
}
KINDS = {"ADJ": "Certificate of adjudication", "WRPERM": "Water use permit"}


def gallons(acre_feet):
    """Acre-feet as gallons in words a reader can picture: 1,234.5 -> '402 million'."""
    amount = acre_feet * GALLONS_PER_ACRE_FOOT
    if amount >= 1e9:
        return f"{amount / 1e9:.1f} billion"
    if amount >= 1e6:
        return f"{amount / 1e6:.0f} million"
    return f"{amount:,.0f}"


def right_label(type_and_number):
    """'ADJ5311' -> ('Certificate of adjudication', '5311')."""
    for prefix, kind in KINDS.items():
        if type_and_number.startswith(prefix):
            return kind, type_and_number[len(prefix):]
    return "Water right", type_and_number


def reported_use(right_ids):
    """{right id: (uses, latest year, acre-feet diverted that year)} from TCEQ's water use table."""
    summary = {}
    ids = sorted(right_ids)
    for i in range(0, len(ids), 40):
        where = "WR_ID IN ({})".format(",".join(f"'{r}'" for r in ids[i:i + 40]))
        offset = 0
        while True:
            rows = fetch_json(USE, {"where": where, "outFields": "WR_ID,USE_NAME,YEAR,TOTAL", "orderByFields": "OBJECTID",
                                    "resultOffset": offset, "resultRecordCount": 2000, "f": "json"})["features"]
            for r in (f["attributes"] for f in rows):
                entry = summary.setdefault(r["WR_ID"], {"uses": set(), "years": {}})
                # One right can list several uses in one field, in varying order and capitals
                entry["uses"].update(u.strip().capitalize() for u in (r["USE_NAME"] or "").split(",") if u.strip())
                if r["YEAR"] and str(r["YEAR"]).isdigit():
                    entry["years"][int(r["YEAR"])] = entry["years"].get(int(r["YEAR"]), 0) + (r["TOTAL"] or 0)
            if len(rows) < 2000:
                break
            offset += 2000
    return summary


def main():
    area = county_boundary(BUFFER_MILES)
    west, south, east, north = area.total_bounds
    features = fetch_json(POINTS, {
        "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outSR": 4326, "f": "geojson",
        "outFields": "TCEQ_ID,TYPE,WR_ID,WR_TYPE_NO"}, timeout=300)["features"]
    if not features:
        sys.exit("TCEQ returned no water rights for the Grimes County area. Nothing written.")
    raw = gpd.clip(gpd.GeoDataFrame.from_features(features, crs=4326), area)
    use = reported_use(set(raw["WR_ID"]))

    rows = []
    for r in raw.itertuples():
        kind, number = right_label(r.WR_TYPE_NO)
        info = use.get(r.WR_ID, {"uses": set(), "years": {}})
        latest = max(info["years"]) if info["years"] else None
        rows.append({
            "name": f"Water right {number}",
            "description": TYPES.get(r.TYPE, r.TYPE),
            "Point type": TYPES.get(r.TYPE, r.TYPE),
            "Right": f"{kind} {number}",
            "Use": ", ".join(sorted(info["uses"])) or None,
            "Latest reported year": latest,
            "Acre-feet diverted that year": round(info["years"][latest], 1) if latest else None,
            "Gallons diverted that year": gallons(info["years"][latest]) if latest else None,
            "id": str(r.TCEQ_ID),
            "geometry": r.geometry,
        })
    rights = gpd.GeoDataFrame(rows, crs=4326).sort_values(["Right", "id"]).reset_index(drop=True)

    size = publish_geojson("water_rights", "Water rights", OUT, rights, POINTS, ("point", "points"), key="id")
    print(f"Wrote {len(rights)} points for {rights['Right'].nunique()} water rights to {OUT.name} ({size / 1024:.0f} KB).")
    for kind, n in rights["Point type"].value_counts().items():
        print(f"  {n:>3}  {kind}")


if __name__ == "__main__":
    main()
