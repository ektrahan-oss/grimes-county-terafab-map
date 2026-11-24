"""Pull land listed under project-linked companies into data/project_holdings.geojson.

Source: Grimes Central Appraisal District. The list of accounts comes from the district's property
search, which is kept more current than its map. Shapes come from the district's map service, asked
for by account number. Accounts the map has no shape of its own for are, so far, drawn inside a
neighboring account's shape: the map merges them. Each is counted with the shape that has room for it.
The shapes are merged into contiguous blocks, so the layer shows each block of land rather than its
tract lines. Road strips between tracts are closed up the same way the reinvestment zone outline is.

Only companies tied to the project are looked up. No other owner is ever requested: the property
search is asked for the names in HOLDERS only, and the map service for account numbers only.

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
import re
from datetime import date
import sys

import geopandas as gpd
import shapely

from build_reinvestment_zone import CLOSE_GAPS_FEET, SIMPLIFY_FEET, drop_small_holes, polygons
from common import DATA, WORK_CRS, _rounded, fetch_json, record_layer, report_change

SERVICE = ("https://utility.arcgis.com/usrsvcs/servers/c35ffea8b2da4f9a84aa5034736b026b/rest/services/"
           "GrimesCADWebService/FeatureServer/0/query")
PROPERTY_SEARCH = "https://grimescad.org/ProxyT/Search/Properties/"      # the district's property search; lists every account under a name
SOURCE = "Grimes Central Appraisal District property search and parcel map (https://grimescad.org/)"
# Name as it appears in the records (matched anywhere in the owner field) -> name shown on the map
HOLDERS = {
    "WIT TECH": "WIT TECH LLC",
    "SPACE EXPLORATION": "Space Exploration Technologies Corp.",
    "SPACEX": "Space Exploration Technologies Corp.",
    "TERAFAB": "TeraFab AI, LLC",
}
OUT = DATA / "project_holdings.geojson"
CLERK = "https://grimes.tx.publicsearch.us/"       # Grimes County Clerk, official records search
CLERK_CHECKED = "November 23, 2026"                # the day the Clerk's records were last searched by hand

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
         "As of": CLERK_CHECKED,
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
         "Ownership": "Still owned by its owner. No deed for it has been recorded.",
         "Nearby": "Among five tracts WIT TECH LLC bought in September and October, near State Highway 90 and FM 39. A 345 kV power line crosses the area.",
         "Source": "Grimes County Clerk records; Grimes Central Appraisal District",
         "As of": CLERK_CHECKED,
         "url": CLERK, "link": "Search the County Clerk's records"}},
]
# Deeds to a project company recorded with the County Clerk but not yet in appraisal records. Outlines in data/sources/,
# matched to appraisal parcels by survey abstract and acreage, with no parcel numbers or owner names.
RECORDED = [
    {"outline": DATA / "sources" / "deed_2026_358467.geojson", "acres": 349.15, "survey": "P O'Connor Survey, Abstract 367",
     "recorded": "October 21, 2026", "document": "2026-358467", "unsure": "A deed to WIT TECH LLC for 349.15 acres in this survey is recorded, but which land it covers is "
     "not confirmed. The outline is the two appraisal tracts that best match it, listed at 346.39 acres together. It may be wrong."},
    {"outline": DATA / "sources" / "deed_2026_358613.geojson", "acres": 189.32, "survey": "B F Smith Survey, Abstract 412, and Geo Galaspy Survey, Abstract 219",
     "recorded": "October 27, 2026", "document": "2026-358613"},
    {"outline": DATA / "sources" / "deed_2026_358697.geojson", "acres": 15.402, "survey": "B.B.B. & C. RR Survey, Abstract 139",
     "recorded": "October 30, 2026", "document": "2026-358697"},
    {"outline": DATA / "sources" / "deed_2026_358702.geojson", "acres": 7.241, "survey": "P Goodbread Survey, Abstract 223",
     "recorded": "October 30, 2026", "document": "2026-358702"},
    {"outline": DATA / "sources" / "deed_2026_358804.geojson", "acres": 20.602, "survey": "B F Arnold Survey, Abstract 67",
     "recorded": "November 4, 2026", "document": "2026-358804"},
    {"outline": DATA / "sources" / "deed_2026_358811.geojson", "acres": 1.094, "survey": "B F Arnold Survey, Abstract 67",
     "recorded": "November 5, 2026", "document": "2026-358811"},
    {"outline": DATA / "sources" / "deed_2026_358774.geojson", "acres": 306.337, "survey": "J Massie Survey, Abstract 333, J W Scott Survey, Abstract 417, and W McCoy Survey, Abstract 320",
     "recorded": "November 3, 2026", "document": "2026-358774",
     "note": "Deed to WIT TECH LLC recorded with the County Clerk. The appraisal district has not yet posted the sale. The deed is for 317.23 acres in all. "
             "That includes a shared interest in a 10-acre tract, which is not drawn or counted here."},
    {"outline": DATA / "sources" / "deed_2026_359007.geojson", "part_of": 99.31, "survey": "W Fitzgibbons Survey, Abstract 15, and B F Smith Survey, Abstract 412",
     "recorded": "November 17, 2026", "document": "2026-359007", "unsure": "A deed to WIT TECH LLC for part of a 99.31-acre tract is recorded. How much of the tract "
     "it covers, and which part, is not confirmed. The outline is the appraisal tracts that best match the larger tract. It may be wrong."},
    {"outline": DATA / "sources" / "deed_2026_359047.geojson", "acres": 163.65, "survey": "J Harris Survey, Abstract 26",
     "recorded": "November 18, 2026", "document": "2026-359047"},
]
# Deeds to a project company in the Clerk's index that cannot be placed on the map yet: the index gives "part of" a larger tract,
# several properties, or an acreage that matches no tract closely. Counted in the legend, not drawn. Move each to RECORDED once located,
# or delete it once appraisal records show the land.
NOT_PLACED = []
SOLD_SHARE = 0.9      # an option is dropped once this share of its land shows under a project company


def property_accounts():
    """Accounts the district's property search lists under the project companies: account number, holder, acres, survey.

    Only the account number, owner name and legal description are kept. Values and mailing addresses are not read.
    """
    year, rows = date.today().year, {}
    for name, shown in HOLDERS.items():
        results, page = [], 1
        while True:         # the search answers a page at a time; a short page would silently drop accounts
            found = fetch_json(PROPERTY_SEARCH, {"f": name, "ty": year, "pvty": year, "pt": "RP;PP;MH;NR", "ps": 200, "pn": page}, timeout=180)
            results += found.get("ResultList") or []
            if not found.get("HasMoreData") or not found.get("ResultList"):
                break
            page += 1
        if len(results) != (found.get("RecordCount") or 0):
            sys.exit(f"The property search says {found.get('RecordCount')} accounts match {name} but returned {len(results)}. Nothing written.")
        for r in results:
            if name not in (r.get("OwnerName") or "").upper():       # the search also matches other fields; keep owners only
                continue
            text = r.get("LegalDescription") or ""
            acres = re.search(r"ACRES\s+(\.?\d[\d.]*)", text)
            rows[r["PropertyNumber"].strip()] = {"account": r["PropertyNumber"].strip(), "holder": shown, "acres": float(acres.group(1)) if acres else 0.0,
                                                 "survey": text.split(" ")[0]}     # an account with no acreage is a building only, such as a mobile home
    if not rows:
        sys.exit("The appraisal district's property search returned no accounts for the project-linked companies. Nothing written.")
    return list(rows.values())


def fetch_parcels():
    """Shapes for the listed accounts, with every account's acres attached to a shape.

    Returns (shapes, accounts not drawn). Each shape carries its own account's acres plus those of accounts
    drawn inside it ("extra"). An account is left as not drawn only when no shape has room for it.
    """
    accounts = property_accounts()
    land = [a for a in accounts if a["acres"] > 0]
    ids = [a["account"] for a in land]
    variants = ids + [" " + i for i in ids]          # the map service pads some account numbers with a space
    features = []
    for i in range(0, len(variants), 60):
        found = fetch_json(SERVICE, {"where": "geo_id IN ({})".format(",".join(f"'{v}'" for v in variants[i:i + 60])),
                                     "outFields": "prop_id,geo_id", "outSR": 4326, "f": "geojson"}, timeout=300)
        if found.get("exceededTransferLimit") or found.get("properties", {}).get("exceededTransferLimit"):
            sys.exit("The appraisal district returned only part of the list. Nothing written.")
        features += found.get("features") or []
    if not features:
        sys.exit("The appraisal district's map returned no shapes for the listed accounts. Nothing written.")
    parcels = gpd.GeoDataFrame.from_features(features, crs=4326).to_crs(WORK_CRS)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty]
    parcels["account"] = parcels["geo_id"].str.strip()
    parcels["geometry"] = parcels.geometry.make_valid()
    parcels = parcels.dissolve("account").reset_index()
    by_account = {a["account"]: a for a in land}
    parcels["holder"] = [by_account[a]["holder"] for a in parcels["account"]]
    parcels["survey"] = [by_account[a]["survey"] for a in parcels["account"]]
    parcels["legal_acreage"] = [by_account[a]["acres"] for a in parcels["account"]]
    parcels["extra_accounts"], parcels["extra_acres"] = 0, 0.0

    # Accounts with no shape of their own: the map draws their land inside a neighbor's shape, which then measures larger
    # than its listed acres. Give each to the shape with the most unexplained room, in its own survey first.
    room = parcels.area / 43560 - parcels["legal_acreage"]
    not_drawn = []
    for a in sorted((a for a in land if a["account"] not in set(parcels["account"])), key=lambda a: -a["acres"]):
        fits = room[room >= 0.75 * a["acres"]]
        same = fits[parcels.loc[fits.index, "survey"] == a["survey"]]
        pick = (same if len(same) else fits)
        pick = pick[parcels.loc[pick.index, "holder"] == a["holder"]]
        if not len(pick):
            not_drawn.append(a)
            continue
        i = pick.idxmax()
        parcels.loc[i, "extra_accounts"] += 1
        parcels.loc[i, "extra_acres"] += a["acres"]
        room[i] -= a["acres"]
    parcels.attrs["buildings_only"] = len(accounts) - len(land)
    return parcels, not_drawn


def blocks_for(parcels):
    """Merge one holder's parcels into contiguous blocks.

    Returns a list of (shape, account count, listed acres, mapped acres). Listed acres are the district's
    figure for each account; mapped acres are measured from the shapes. They can differ.
    """
    land = parcels.geometry.union_all()
    closed = land.buffer(CLOSE_GAPS_FEET, join_style="mitre").buffer(-CLOSE_GAPS_FEET, join_style="mitre")
    merged = drop_small_holes(shapely.unary_union([closed, land]))
    middles = parcels.geometry.representative_point()
    out = []
    for block in polygons(merged):
        inside = parcels[middles.within(block)]
        shape = shapely.make_valid(block.simplify(SIMPLIFY_FEET, preserve_topology=True))
        out.append((shape, int(len(inside) + inside["extra_accounts"].sum()), float((inside["legal_acreage"] + inside["extra_acres"]).sum()),
                    float(inside.area.sum() / 43560)))
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


def layer_note(parcels, zone, not_drawn):
    """Sentences for the map legend: totals as of today, and how much of the land lies outside the zone."""
    n = int(len(parcels) + parcels["extra_accounts"].sum()) + len(not_drawn)
    listed = float((parcels["legal_acreage"] + parcels["extra_acres"]).sum()) + sum(a["acres"] for a in not_drawn)
    totals = (f"As of {date.today():%B} {date.today().day}, {date.today().year}: {n} accounts and about {round(listed, -1):,.0f} acres owned, "
              "from the appraisal district's property search.")
    if parcels.attrs.get("buildings_only"):
        k = parcels.attrs["buildings_only"]
        totals += f" {k} more account{' is a building' if k == 1 else 's are buildings'} with no land."
    if not_drawn:
        totals += (f" {len(not_drawn)} of the accounts, about {sum(a['acres'] for a in not_drawn):,.0f} acres, are not drawn: "
                   "the district's map has no shape for them yet.")
    if zone is None:
        return totals
    land = parcels.geometry.union_all()
    mapped, outside = land.area / 43560, land.difference(zone).area / 43560
    return (f"{totals} About {round(outside, -1):,.0f} of the roughly {round(mapped, -1):,.0f} mapped acres "
            f"({outside / mapped:.0%}) lie outside the reinvestment zone.")


def totals(features):
    count = lambda p: p.get("Accounts", p.get("Parcels"))       # blocks were counted in parcels before the property search was used
    owned = [f for f in features if f["properties"].get("Status") == OWNED and count(f["properties"]) is not None]
    return (sum(count(f["properties"]) for f in owned), sum(f["properties"]["Acres listed"] for f in owned))


def feature(shape, props):
    g = json.loads(gpd.GeoSeries([shape], crs=WORK_CRS).to_crs(4326).to_json())["features"][0]["geometry"]
    g["coordinates"] = _rounded(g["coordinates"])
    return {"type": "Feature", "properties": props, "geometry": g}


def sold(shape, owned):
    """True once appraisal records show the land under a project company."""
    if shape.geom_type == "Point":
        return False        # an approximate point can sit inside a neighbor that was bought; remove these by hand when a deed is recorded
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
            "description": deed.get("unsure") or deed.get("note") or "Deed to WIT TECH LLC recorded with the County Clerk. The appraisal district has not yet posted the sale.",
            "Status": UNSURE if deed.get("unsure") else OWNED,
            **({"Reinvestment zone": status} if status else {}),
            "Bought by": "WIT TECH LLC",
            "Acres in the deed": f"{deed['acres']:,}" if "acres" in deed else f"Part of a {deed['part_of']:,}-acre tract. The amount is not confirmed.",
            "Survey": deed["survey"],
            "Recorded": f"{deed['recorded']}, Grimes County Clerk document {deed['document']}",
            "Appraisal records": ("Still list this land under another owner. The match between the deed and these tracts is a best guess "
                                  "from survey and acreage, and is not confirmed." if deed.get("unsure") else
                                  "Not yet updated. The outline is the appraisal district's tract matched to the deed by survey and acreage. Approximate, not a survey."),
            "Source": "Grimes County Clerk records; Grimes Central Appraisal District",
            "As of": CLERK_CHECKED,
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
    parcels, not_drawn = fetch_parcels()
    rows = []
    for holder, group in parcels.groupby("holder"):
        for shape, count, acres, mapped in blocks_for(group):
            rows.append({"name": "Project-linked holding", "Status": OWNED, "Reinvestment zone": zone_status(shape, zone), "Listed under": holder, "Accounts": count,
                         "Acres listed": round(acres, 1), "Acres mapped": round(mapped, 1), "geometry": shape,
                         "description": "Land listed under this company in appraisal district records. "
                                        "Tract lines inside the block are merged. Listed acres are the district's figure; "
                                        "mapped acres are measured from the shapes. Approximate, not a survey."})
    if sum(r["Accounts"] for r in rows) != len(parcels) + parcels["extra_accounts"].sum():
        sys.exit("Some accounts did not land in a block. Nothing written.")
    blocks = gpd.GeoDataFrame(rows, crs=WORK_CRS).sort_values("Acres listed", ascending=False)

    features = json.loads(blocks.to_crs(4326).to_json(drop_id=True))["features"]
    for f in features:
        f["geometry"]["coordinates"] = _rounded(f["geometry"]["coordinates"])
    recorded = recorded_features(parcels, zone)
    options = option_features(parcels)
    features += recorded + options          # drawn after the owned blocks, with their own status
    old = json.loads(OUT.read_text(encoding="utf-8"))["features"] if OUT.exists() else None
    lines = ",\n".join("    " + json.dumps(f, ensure_ascii=False, separators=(", ", ": ")) for f in features)
    note = layer_note(parcels, zone, not_drawn)
    if recorded:
        stated = [float(f["properties"]["Acres in the deed"].replace(",", "")) for f in recorded if not f["properties"]["Acres in the deed"].startswith("Part")]
        parts = len(recorded) - len(stated)
        guesses = sum(1 for f in recorded if f["properties"]["Status"] == UNSURE)
        note += f" Also shown: {len(recorded)} deeds recorded with the County Clerk that appraisal records do not yet show. {len(stated)} of them cover about {sum(stated):,.0f} acres."
        if parts:
            note += (" The other one covers part of a larger tract, and the amount is not confirmed." if parts == 1 else
                     f" The other {parts} cover part of larger tracts, and the amount is not confirmed.")
        if guesses:
            note += f" For {guesses} of the {len(recorded)}, the exact tract is not confirmed and the outline is a best guess."
    if NOT_PLACED:
        if len(NOT_PLACED) == 1:
            note += f" 1 more deed, recorded {NOT_PLACED[0]['recorded']}, is not drawn yet: it covers part of a larger tract and the acreage is not confirmed."
        else:
            note += (f" {len(NOT_PLACED)} more deeds recorded from {NOT_PLACED[0]['recorded']} to {NOT_PLACED[-1]['recorded']} are not drawn yet: "
                     "the acreage or the tract is not confirmed.")
    note += f" County Clerk records searched {CLERK_CHECKED}."
    head = '{\n  "type": "FeatureCollection",\n' + (f'  "note": {json.dumps(note)},\n' if note else "")
    OUT.write_text(head + '  "features": [\n' + lines + "\n  ]\n}\n", encoding="utf-8")

    n, acres = totals(features)
    now = f"{n} accounts and about {acres:,.0f} acres in {len(features) - len(options) - len(recorded)} blocks"
    if old is None:
        summary = f"new, with {now}"
    elif totals(old) != totals(features):
        was_n, was_acres = totals(old)
        summary = f"now {now} (was {was_n} accounts and about {was_acres:,.0f} acres)"
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
            print(f"  {p['Acres in the deed'][:24]:>9} acres  deed recorded {p['Recorded']}, {p['Status']}")
        elif p.get("Status") == OPTIONED:
            print(f"  {p['Acres in the option']:>9} acres  under option to {p['Option held by']}, not yet sold")
        else:
            print(f"  {p['Acres listed']:>9,.1f} acres listed  {p['Acres mapped']:>9,.1f} mapped  {p['Accounts']:>2} accounts  {p['Listed under']}")
    for a in not_drawn:
        print(f"  not drawn: an account of {a['acres']:,.2f} acres in survey {a['survey']}")


if __name__ == "__main__":
    main()
