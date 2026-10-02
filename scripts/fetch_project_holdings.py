"""Pull land listed under project-linked companies into data/project_holdings.geojson.

Source: Grimes Central Appraisal District, current parcel records. Parcels listed under the
companies named in HOLDERS are merged into contiguous blocks, so the layer shows each block of
land rather than its tract lines. Road strips between tracts are closed up the same way the
reinvestment zone outline is.

Only companies tied to the project are looked up. No other owner is ever requested.

Run by hand when you want to check for new purchases:

    python scripts/fetch_project_holdings.py
"""

import json
import sys

import geopandas as gpd
import shapely

from build_reinvestment_zone import CLOSE_GAPS_FEET, SIMPLIFY_FEET, drop_small_holes, polygons
from common import DATA, WORK_CRS, _rounded, fetch_json, record_layer, report_change

SERVICE = ("https://utility.arcgis.com/usrsvcs/servers/c35ffea8b2da4f9a84aa5034736b026b/rest/services/"
           "GrimesCADWebService/FeatureServer/0/query")
SOURCE = "Grimes Central Appraisal District parcel records (https://gis.bisclient.com/grimescad/)"
# Name as it appears in the records (matched anywhere in the owner field) -> name shown on the map
HOLDERS = {
    "WIT TECH": "WIT TECH LLC",
    "SPACE EXPLORATION": "Space Exploration Technologies Corp.",
    "SPACEX": "Space Exploration Technologies Corp.",
    "TERAFAB": "TeraFab AI, LLC",
}
OUT = DATA / "project_holdings.geojson"


def fetch_parcels():
    where = " OR ".join(f"UPPER(file_as_name) LIKE '%{name}%'" for name in HOLDERS)
    found = fetch_json(SERVICE, {"where": where, "outFields": "prop_id,file_as_name,legal_acreage",
                                 "outSR": 4326, "f": "geojson"}, timeout=300)
    if found.get("exceededTransferLimit") or found.get("properties", {}).get("exceededTransferLimit"):
        sys.exit("The appraisal district returned only part of the list. Nothing written.")
    if not found.get("features"):
        sys.exit("The appraisal district returned no parcels for the project-linked companies. Nothing written.")
    parcels = gpd.GeoDataFrame.from_features(found["features"], crs=4326).to_crs(WORK_CRS)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty].drop_duplicates(subset="prop_id")
    listed = parcels["file_as_name"].str.upper()
    parcels["holder"] = [next(shown for name, shown in HOLDERS.items() if name in owner) for owner in listed]
    parcels["geometry"] = parcels.geometry.make_valid()
    return parcels


def blocks_for(parcels):
    """Merge one holder's parcels into contiguous blocks.

    Returns a list of (shape, parcel count, listed acres, mapped acres). Listed acres are the district's
    figure for each parcel; mapped acres are measured from the parcel shapes. They can differ.
    """
    land = parcels.geometry.union_all()
    closed = land.buffer(CLOSE_GAPS_FEET, join_style="mitre").buffer(-CLOSE_GAPS_FEET, join_style="mitre")
    merged = drop_small_holes(shapely.unary_union([closed, land]))
    middles = parcels.geometry.representative_point()
    out = []
    for block in polygons(merged):
        inside = parcels[middles.within(block)]
        shape = shapely.make_valid(block.simplify(SIMPLIFY_FEET, preserve_topology=True))
        out.append((shape, len(inside), float(inside["legal_acreage"].fillna(0).sum()), float(inside.area.sum() / 43560)))
    return out


def outside_zone_note(parcels):
    """One sentence for the map legend: how much of the mapped land lies outside the reinvestment zone outline."""
    path = DATA / "reinvestment_zone.geojson"
    if not path.exists():
        return None
    zone = gpd.read_file(path).to_crs(WORK_CRS).geometry.union_all()
    land = parcels.geometry.union_all()
    mapped, outside = land.area / 43560, land.difference(zone).area / 43560
    return (f"About {round(outside, -1):,.0f} of the roughly {round(mapped, -1):,.0f} mapped acres "
            f"({outside / mapped:.0%}) lie outside the reinvestment zone.")


def totals(features):
    return (sum(f["properties"]["Parcels"] for f in features), sum(f["properties"]["Acres listed"] for f in features))


def main():
    parcels = fetch_parcels()
    rows = []
    for holder, group in parcels.groupby("holder"):
        for shape, count, acres, mapped in blocks_for(group):
            rows.append({"name": "Project-linked holding", "Listed under": holder, "Parcels": count,
                         "Acres listed": round(acres, 1), "Acres mapped": round(mapped, 1), "geometry": shape,
                         "description": "Land listed under this company in appraisal district records. "
                                        "Tract lines inside the block are merged. Listed acres are the district's figure; "
                                        "mapped acres are measured from the shapes. Approximate, not a survey."})
    if sum(r["Parcels"] for r in rows) != len(parcels):
        sys.exit("Some parcels did not land in a block. Nothing written.")
    blocks = gpd.GeoDataFrame(rows, crs=WORK_CRS).sort_values("Acres listed", ascending=False)

    features = json.loads(blocks.to_crs(4326).to_json(drop_id=True))["features"]
    for f in features:
        f["geometry"]["coordinates"] = _rounded(f["geometry"]["coordinates"])
    old = json.loads(OUT.read_text(encoding="utf-8"))["features"] if OUT.exists() else None
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False, separators=(", ", ": ")) for f in features)
    note = outside_zone_note(parcels)
    head = '{\n  "type": "FeatureCollection",\n' + (f'  "note": {json.dumps(note)},\n' if note else "")
    OUT.write_text(head + '  "features": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")

    n, acres = totals(features)
    now = f"{n} parcels and about {acres:,.0f} acres in {len(features)} blocks"
    if old is None:
        summary = f"new, with {now}"
    elif old != features:
        was_n, was_acres = totals(old)
        summary = f"now {now} (was {was_n} parcels and about {was_acres:,.0f} acres)"
    else:
        summary = None
    report_change("holdings", "Project-linked holdings", summary)
    record_layer("holdings", SOURCE, len(features))

    print(f"Wrote {len(features)} blocks to {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB): {now}.")
    if note:
        print(f"  {note}")
    for f in features:
        p = f["properties"]
        print(f"  {p['Acres listed']:>9,.1f} acres  {p['Parcels']:>2} parcels  {p['Listed under']}")


if __name__ == "__main__":
    main()
