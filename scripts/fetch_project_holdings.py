"""Pull land listed under project-linked companies into data/project_holdings.geojson.

Source: Grimes Central Appraisal District, current parcel records. Parcels listed under the
companies named in HOLDERS are merged into contiguous blocks, so the layer shows each block of
land rather than its tract lines. Road strips between tracts are closed up the same way the
reinvestment zone outline is.

Only companies tied to the project are looked up. No other owner is ever requested.

Land a project company holds an option on, but has not bought, is added from OPTIONS below. Its
outline is a hand-drawn file in data/sources/ with no parcel numbers or owner names, or a single
approximate point when the optioned piece has not been located. An option is shown with its own
status and drops off by itself once the land appears under a project company.

Deeds recorded with the County Clerk that the appraisal district has not yet posted are added from
RECORDED below, the same way. Each drops off by itself once the appraisal records show the land.

Run by hand when you want to check for new purchases:

    python scripts/fetch_project_holdings.py
"""

import json
from datetime import date
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
CLERK = "https://grimes.tx.publicsearch.us/"       # Grimes County Clerk, official records search

OWNED, OPTIONED = "Owned", "Under option (not yet sold)"
UNSURE = "Bought, exact tract not yet confirmed"
# Options recorded with the County Clerk, from the Clerk's records. Give an outline, or a point when the piece is not yet located.
OPTIONS = [
    {"outline": DATA / "sources" / "reservoir_option_tract.geojson", "acres": 3135.31,
     "properties": {
         "name": "Under option, not yet sold",
         "description": "Gibbons Creek Reservoir and its shoreline. A project company holds a recorded option to buy this land. It has not been sold.",
         "Status": OPTIONED,
         "Option held by": "WIT TECH LLC",
         "Acres in the option": "3,135.31",
         "Option signed": "May 27, 2026",
         "Recorded": "May 28, 2026, Grimes County Clerk document 2026-355105",
         "Contract of sale effective": "February 6, 2026",
         "Ownership": "Still owned by the reservoir's owner. Appraisal records show no sale.",
         "Water rights": "The option does not appear to cover the state water rights, which state records list under the reservoir's owner.",
         "Source": "Grimes County Clerk, Memorandum of Option; Grimes Central Appraisal District",
         "As of": "October 8, 2026",
         "url": CLERK, "link": "Search the County Clerk's records"}},
    {"point": (-95.9625, 30.6501), "acres": 6.757,
     "properties": {
         "name": "Under option, not yet sold",
         "description": "A project company holds a recorded option to buy 6.757 acres here. It has not been sold. The point is approximate: "
                        "the optioned piece lies in the land around this point, but its exact boundaries have not been located.",
         "Status": OPTIONED,
         "Option held by": "WIT TECH LLC",
         "Acres in the option": "6.757",
         "Survey": "J W Singletary Survey, Abstract 441",
         "Recorded": "October 7, 2026, Grimes County Clerk document 2026-358239",
         "Ownership": "Still owned by its owner. Appraisal records show no sale.",
         "Nearby": "Next to four tracts WIT TECH LLC bought in September, near State Highway 90 and FM 39. A 345 kV power line crosses the area.",
         "Source": "Grimes County Clerk records; Grimes Central Appraisal District",
         "As of": "October 19, 2026",
         "url": CLERK, "link": "Search the County Clerk's records"}},
]
# Deeds to a project company recorded with the County Clerk but not yet in appraisal records. Outlines in data/sources/,
# matched to appraisal parcels by survey abstract and acreage, with no parcel numbers or owner names.
RECORDED = [
    {"outline": DATA / "sources" / "deed_2026_358331.geojson", "acres": 502.87, "survey": "P O'Connor Survey, Abstract 367",
     "recorded": "October 13, 2026", "document": "2026-358331"},
    {"outline": DATA / "sources" / "deed_2026_358325.geojson", "acres": 16.097, "survey": "H Frazier Survey, Abstract 214",
     "recorded": "October 13, 2026", "document": "2026-358325"},
    {"outline": DATA / "sources" / "deed_2026_358323.geojson", "acres": 5.645, "survey": "P O'Connor Survey, Abstract 367",
     "recorded": "October 13, 2026", "document": "2026-358323"},
    {"outline": DATA / "sources" / "deed_2026_358238_either_tract.geojson", "acres": 62.174, "survey": "W McCoy Survey, Abstract 320",
     "recorded": "October 7, 2026", "document": "2026-358238", "unsure": "The deed covers one of the two tracts of about 62 acres "
     "inside this outline. Which one is not yet confirmed, so both are outlined."},
]
SOLD_SHARE = 0.9      # an option is dropped once this share of its land shows under a project company


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


ZONE_FILE = DATA / "reinvestment_zone.geojson"
MOSTLY = 0.95           # a block with at least this share of its land in the zone is called inside it; at most 1 - MOSTLY, outside


def zone_outline():
    return gpd.read_file(ZONE_FILE).to_crs(WORK_CRS).geometry.union_all() if ZONE_FILE.exists() else None


def zone_status(shape, zone):
    """Pop-up line saying where a block sits against the reinvestment zone."""
    if zone is None:
        return None
    share = shape.intersection(zone).area / shape.area
    if share >= MOSTLY:
        return "Inside the reinvestment zone"
    if share <= 1 - MOSTLY and shape.distance(zone) < 0.05 * 5280:
        return "Just outside the reinvestment zone, against its line"
    if share <= 1 - MOSTLY:
        return f"Outside the reinvestment zone, {shape.distance(zone) / 5280:.1f} miles from its line"
    return f"Partly inside the reinvestment zone ({share:.0%} of its land inside)"


def layer_note(parcels, zone):
    """Sentences for the map legend: totals as of today, and how much of the land lies outside the zone."""
    n, listed = len(parcels), parcels["legal_acreage"].fillna(0).sum()
    totals = f"As of {date.today():%B} {date.today().day}, {date.today().year}: {n} parcels and about {round(listed, -1):,.0f} acres owned."
    if zone is None:
        return totals
    land = parcels.geometry.union_all()
    mapped, outside = land.area / 43560, land.difference(zone).area / 43560
    return (f"{totals} About {round(outside, -1):,.0f} of the roughly {round(mapped, -1):,.0f} mapped acres "
            f"({outside / mapped:.0%}) lie outside the reinvestment zone.")


def totals(features):
    owned = [f for f in features if f["properties"].get("Status") == OWNED and "Parcels" in f["properties"]]
    return (sum(f["properties"]["Parcels"] for f in owned), sum(f["properties"]["Acres listed"] for f in owned))


def feature(shape, props):
    g = json.loads(gpd.GeoSeries([shape], crs=WORK_CRS).to_crs(4326).to_json())["features"][0]["geometry"]
    g["coordinates"] = _rounded(g["coordinates"])
    return {"type": "Feature", "properties": props, "geometry": g}


def sold(shape, owned):
    """True once appraisal records show the land under a project company."""
    if shape.geom_type == "Point":
        return shape.within(owned.buffer(50))
    return shape.intersection(owned).area / shape.area >= SOLD_SHARE


def recorded_features(parcels, zone):
    """Features for recorded deeds the appraisal district has not yet posted."""
    owned = parcels.geometry.union_all()
    out = []
    for deed in RECORDED:
        shape = gpd.read_file(deed["outline"]).to_crs(WORK_CRS).geometry.union_all()
        if sold(shape, owned):
            print(f"  Recorded deed left off: appraisal records now show it ({deed['document']}). Remove it from RECORDED.")
            continue
        status = zone_status(shape, zone)
        out.append(feature(shape, {
            "name": "Project-linked holding",
            "description": deed.get("unsure") or "Deed to WIT TECH LLC recorded with the County Clerk. The appraisal district has not yet posted the sale.",
            "Status": UNSURE if deed.get("unsure") else OWNED,
            **({"Reinvestment zone": status} if status else {}),
            "Bought by": "WIT TECH LLC",
            "Acres in the deed": f"{deed['acres']:,}",
            "Survey": deed["survey"],
            "Recorded": f"{deed['recorded']}, Grimes County Clerk document {deed['document']}",
            "Appraisal records": "Not yet updated. The outline is the appraisal district's tract matched to the deed by survey and acreage. Approximate, not a survey.",
            "Source": "Grimes County Clerk records; Grimes Central Appraisal District",
            "As of": "October 19, 2026",
            "url": CLERK, "link": "Search the County Clerk's records"}))
    return out


def option_features(parcels):
    """Features for recorded options whose land has not yet passed to a project company."""
    owned = parcels.geometry.union_all()
    out = []
    for option in OPTIONS:
        if "point" in option:
            shape = gpd.GeoSeries([shapely.Point(option["point"])], crs=4326).to_crs(WORK_CRS).iloc[0]
        else:
            shape = gpd.read_file(option["outline"]).to_crs(WORK_CRS).geometry.union_all()
        if sold(shape, owned):
            print(f"  Option left off: the land now shows under a project company ({option['properties']['Recorded']}). Remove it from OPTIONS.")
            continue
        props = dict(option["properties"])
        status = None if shape.geom_type == "Point" else zone_status(shape, zone_outline())
        props = {**{k: v for k, v in props.items() if k in ("name", "description", "Status")}, **({"Reinvestment zone": status} if status else {}),
                 **{k: v for k, v in props.items() if k not in ("name", "description", "Status")}}
        out.append(feature(shape, props))
    return out


def main():
    zone = zone_outline()
    parcels = fetch_parcels()
    rows = []
    for holder, group in parcels.groupby("holder"):
        for shape, count, acres, mapped in blocks_for(group):
            rows.append({"name": "Project-linked holding", "Status": OWNED, "Reinvestment zone": zone_status(shape, zone), "Listed under": holder, "Parcels": count,
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
    recorded = recorded_features(parcels, zone)
    options = option_features(parcels)
    features += recorded + options          # drawn after the owned blocks, with their own status
    old = json.loads(OUT.read_text(encoding="utf-8"))["features"] if OUT.exists() else None
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False, separators=(", ", ": ")) for f in features)
    note = layer_note(parcels, zone)
    if recorded:
        acres = sum(float(f["properties"]["Acres in the deed"].replace(",", "")) for f in recorded)
        note += f" Also shown: {len(recorded)} deeds for about {acres:,.0f} acres recorded with the County Clerk that appraisal records do not yet show."
    head = '{\n  "type": "FeatureCollection",\n' + (f'  "note": {json.dumps(note)},\n' if note else "")
    OUT.write_text(head + '  "features": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")

    n, acres = totals(features)
    now = f"{n} parcels and about {acres:,.0f} acres in {len(features) - len(options) - len(recorded)} blocks"
    if old is None:
        summary = f"new, with {now}"
    elif totals(old) != totals(features):
        was_n, was_acres = totals(old)
        summary = f"now {now} (was {was_n} parcels and about {was_acres:,.0f} acres)"
    elif len(recorded) != sum(1 for f in old if "Acres in the deed" in f["properties"]):
        summary = (f"{len(recorded)} deeds recorded with the County Clerk, not yet in appraisal records, now shown" if recorded
                   else "recorded deeds now show in appraisal records")
    elif len(options) != sum(1 for f in old if f["properties"].get("Status") == OPTIONED):
        option_acres = sum(o["acres"] for o in OPTIONS)
        summary = (f"land under option is now shown: about {option_acres:,.0f} acres, not yet sold" if options
                   else "land that was under option now shows as owned")
    else:
        summary = None
    report_change("holdings", "Project-linked holdings", summary)
    record_layer("holdings", SOURCE, len(features))

    print(f"Wrote {len(features)} blocks to {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB): {now}.")
    if note:
        print(f"  {note}")
    for f in features:
        p = f["properties"]
        if "Acres in the deed" in p:
            print(f"  {p['Acres in the deed']:>9} acres  deed recorded {p['Recorded']}, {p['Status']}")
        elif p.get("Status") == OPTIONED:
            print(f"  {p['Acres in the option']:>9} acres  under option to {p['Option held by']}, not yet sold")
        else:
            print(f"  {p['Acres listed']:>9,.1f} acres  {p['Parcels']:>2} parcels  {p['Listed under']}")


if __name__ == "__main__":
    main()
