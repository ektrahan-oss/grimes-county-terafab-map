"""Build the Permits & filings layer in data/permits_filings.geojson.

Two kinds of item go on the layer:

  Hand-kept   ITEMS below: permits, filings and purchases checked by hand against the record named
              in each one's Source line. Edit the list to add or change one.
  From the    Records the daily permit watch (watch_permits.py) has found that name a project
  watch       company or the roads at the site. They are read from data/permit_watch.json, so a
              new filing reaches the map the day the watch first sees it. The state's records
              give no coordinates, so these are placed at the project site and say so.

NOT_FILED is the list of filings looked for and not found. A line drops off by itself when the
watch finds that kind of filing; the others are removed by hand. Nothing is looked up when this
script runs: it only reads the two lists.

    python scripts/build_permits_filings.py
"""

import json
import math
import re
from datetime import date

from common import DATA, publish_features

OUT = DATA / "permits_filings.geojson"
SOURCE = "TCEQ Central Registry, TCEQ air permit search, ERCOT GIS Report and Grimes Central Appraisal District records"

REGISTRY = "https://www15.tceq.texas.gov/crpub/index.cfm?fuseaction=regent.validateRE&re_ref_num_txt="
AIR_SEARCH = "https://www2.tceq.texas.gov/airperm/index.cfm?fuseaction=airpermits.start"
ERCOT = "https://www.ercot.com/mp/data-products/data-product-details?id=PG7-200-ER"
APPRAISAL = "https://gis.bisclient.com/grimescad/"

# Status decides the symbol on the map, so use one of these four wordings
ACTIVE, PENDING, PURCHASE, QUEUE = "Active permit", "Pending application", "Land purchase, not a permit", "Energy project in the grid queue"

# (longitude, latitude), then the pop-up's lines in the order they are shown
ITEMS = [
    ((-96.0451, 30.6089), {
        "name": "Construction stormwater permit TXR1535YT",
        "description": "State permit covering stormwater runoff from construction at the Gibbons Creek Project Site.",
        "Status": ACTIVE,
        "Held by": "Space Exploration Technologies Corp., as operator",
        "Active since": "July 5, 2026",
        "Location": "Approximate. The record gives no coordinates: it describes the site as south of Gibbons Creek Reservoir, north of State Highway 30 and east of FM 171. The marker is on project-held land that fits that description.",
        "Source": "TCEQ Central Registry",
        "As of": "October 8, 2026",
        "url": REGISTRY + "RN112483532", "link": "See the TCEQ record",
        "id": "tceq-TXR1535YT", "watch": ["tceq-permit:RN112483532:TXR1535YT", "tceq-site:RN112483532"]}),
    ((-96.0498, 30.5945), {
        "name": "Concrete batch plant air permit, registration 184781",
        "description": "Not filed by SpaceX; a sign of heavy construction nearby.",
        "Status": PENDING,
        "Filed by": "Knife River Corporation - South",
        "Received by the state": "July 23, 2026",
        "Location": "Approximate. Placed from the record's directions: 1.7 miles east of FM 244 on State Highway 30, on the south side.",
        "Source": "TCEQ air permit search and Central Registry",
        "As of": "October 8, 2026",
        "url": REGISTRY + "RN112495213", "link": "See the TCEQ record",
        "id": "tceq-air-184781", "watch": ["air:412457"]}),
    ((-96.15967, 30.65237), {
        "name": "River pump station tracts",
        "description": "Two tracts on the Navasota River, 3.83 and 1.25 acres. WIT TECH LLC bought them; the sale has closed.",
        "Status": PURCHASE,
        "Land owned by": "WIT TECH LLC",
        "Deed date": "May 27, 2026",
        "Improvements on the appraisal record": "None",
        "Water right to pump here": "Separate from the land, and not WIT TECH's on record. As of October 6, 2026 the state still lists the right to pump from the river under the company that owns Gibbons Creek Reservoir.",
        "Source": "Grimes Central Appraisal District; TCEQ water rights records",
        "As of": "October 8, 2026",
        "url": APPRAISAL, "link": "Look it up at the appraisal district",
        "id": "cad-pump-tracts"}),
    ((-95.96906, 30.64589), {
        "name": "Eagle Claw Energy Center",
        "description": "A battery storage project by another company. Its own parcel is inside the reinvestment zone.",
        "Status": QUEUE,
        "ERCOT queue number": "27INR0085",
        "Size": "204.58 megawatts",
        "Grid agreement signed": "September 15, 2025",
        "Projected start": "March 31, 2028",
        "Location": "The company's 15-acre parcel on County Road 176, from appraisal records.",
        "Source": "ERCOT GIS Report published October 1, 2026; Grimes Central Appraisal District",
        "As of": "October 8, 2026",
        "url": ERCOT, "link": "ERCOT's monthly reports",
        "id": "ercot-27INR0085"}),
    ((-95.9433, 30.5837), {
        "name": "Diamante battery project",
        "description": "A battery storage project by another company. Approximate: grid connection point, not the project site.",
        "Status": QUEUE,
        "ERCOT queue number": "27INR0115",
        "Size": "513.68 megawatts",
        "Queue status": "Active again since ERCOT's June 2026 report",
        "Connects at": "Roans Prairie 345 kV",
        "Projected start": "November 1, 2028",
        "Location": "Approximate. ERCOT names a connection point, not a site. The marker is at the community of Roans Prairie.",
        "Source": "ERCOT GIS Report published October 1, 2026",
        "As of": "October 8, 2026",
        "url": ERCOT, "link": "ERCOT's monthly reports",
        "id": "ercot-27INR0115"}),
]

# Filings looked for and not found: (wording, date last checked by hand, how the watch would spot one).
# A line with a pattern is checked every day by the permit watch and drops off when a watch record matches.
# A line with None is only checked by hand: change its date after each look, and delete it when the filing appears.
NOT_FILED = [
    ("an air permit for the on-site power plants", date(2026, 10, 8), r"^air:.*\|SpaceX air permit"),
    ("a wastewater discharge permit", date(2026, 10, 8), r"^tceq-permit:.*\|TCEQ wastewater"),
    ("a water right transfer or amendment", date(2026, 10, 8), None),
    ("a groundwater permit", date(2026, 10, 8), None),
    ("a federal (Army Corps) permit", date(2026, 10, 8), r"^corps.*\|Army Corps .*(naming the project|at the site)"),
]

WATCH_STATE = DATA / "permit_watch.json"
# Watch records that belong on the map: the project's own state records, and air permits and building
# projects that name a project company or the roads at the site. Pipelines, road projects and court
# agenda items stay in the News tab only.
ON_THE_MAP = ("tceq-permit:", "tceq-name:", "air:", "tdlr:", "corps:", "corps-notice:")
SOURCE_NAMES = {"tceq": "TCEQ Central Registry", "air": "TCEQ air permit search", "tdlr": "Texas Department of Licensing and Regulation project registry",
                "corps": "U.S. Army Corps of Engineers, Fort Worth District"}
# Which watch source vouches for each automatic line of NOT_FILED, by the start of its pattern
CHECKED_BY = {"^air": "air", "^tceq": "tceq", "^corps": "corps"}
SITE = (-96.0451, 30.6089)        # where records with no coordinates are placed: project-held land south of the reservoir
RING_DEGREES = 0.006              # about a third of a mile, so several such records do not sit on one spot


def long_date(day):
    return f"{day:%B} {day.day}, {day.year}"


def watch_items(state, checked):
    """Features for watch records that are not already covered by a hand-kept item."""
    covered = {key for _, props in ITEMS for key in props.get("watch", [])}
    # Army Corps records cover the whole county; only those that name the project or lie at the site go on the map
    keys = sorted(k for k in state if k.startswith(ON_THE_MAP) and k not in covered and "missing_since" not in state[k]
                  and (not k.startswith("corps") or state[k].get("at_site")))
    features = []
    for n, key in enumerate(keys):
        item = state[key]
        angle = 2 * math.pi * n / max(len(keys), 6)
        located = "lon" in item and "lat" in item
        ring = [round(SITE[0] + RING_DEGREES * math.cos(angle), 5), round(SITE[1] + RING_DEGREES * math.sin(angle) * 0.86, 5)]
        point = [item["lon"], item["lat"]] if located else ring
        status = item["status"].strip()
        if re.search(r"pending|review|received", status, re.I):
            shown = PENDING
        elif re.search(r"active|issued|effective|complete|registered", status, re.I):
            shown = f"{ACTIVE} ({status.lower()})"
        else:
            shown = status.capitalize()
        what = item["what"]
        title = what if len(what) <= 70 else what.split(":")[0] if ":" in what[:70] else what[:67].rsplit(" ", 1)[0] + "..."
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": point}, "properties": {
            "name": title, "description": what, "Status": shown,
            "First seen by the daily check": long_date(date.fromisoformat(item["first_seen"])),
            "Location": "From the coordinates in the record." if located else
                        "Approximate. The record gives no coordinates, so the marker is placed at the project site.",
            "Source": SOURCE_NAMES[key.split(":")[0].split("-")[0]], "As of": long_date(checked),
            "url": item["link"], "link": "See the record", "id": "watch-" + key}})
    return features


def not_filed_note(state, checked, by_source):
    """The legend's dated list of filings not found, without any the watch has now seen."""
    lines = []
    for wording, by_hand, pattern in NOT_FILED:
        if pattern and any(re.search(pattern, f"{k}|{v['what']}", re.I) for k, v in state.items()):
            continue
        source = next((s for start, s in CHECKED_BY.items() if pattern and pattern.startswith(start)), None)
        last = date.fromisoformat(by_source[source]) if source in by_source else checked if source in ("air", "tceq") else by_hand
        lines.append(f"{wording} (none found as of {long_date(max(last, by_hand))})")
    return ("Not filed yet: " + "; ".join(lines) + ".") if lines else None


def build(state, checked, by_source=None):
    """(features, legend note) from the hand-kept list plus the watch's records.

    checked is the day the watch last ran in full; by_source gives the last day each of its sources answered.
    """
    hand = [{"type": "Feature", "properties": {k: v for k, v in props.items() if k != "watch"},
             "geometry": {"type": "Point", "coordinates": list(point)}} for point, props in ITEMS]
    return hand + watch_items(state, checked), not_filed_note(state, checked, by_source or {})


def main():
    state = json.loads(WATCH_STATE.read_text(encoding="utf-8")) if WATCH_STATE.exists() else {}
    state.pop("_recorded", None)
    by_source = state.pop("_checked", {})
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("layers", {})
    checked = date.fromisoformat(meta["permits"]["updated"]) if "permits" in meta else date.today()
    features, note = build(state, checked, by_source)
    size = publish_features("filings", "Permits and filings", OUT, features, SOURCE, ("item", "items"), key="id", note=note)
    print(f"Wrote {len(features)} items to {OUT.name} ({size / 1024:.1f} KB).")
    for f in features:
        print(f"  {f['properties']['Status']:34} {f['properties']['name']}")
    print(" ", note)


if __name__ == "__main__":
    main()
