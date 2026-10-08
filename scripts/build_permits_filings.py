"""Build the Permits & filings layer in data/permits_filings.geojson.

A short, hand-kept list of state permits and filings tied to the project or to construction near
it, each checked against the record named in its Source line. Nothing is looked up when this runs:
to add or change an item, edit ITEMS below, then run

    python scripts/build_permits_filings.py

Each item carries its source, a link to it and the date it was last checked. Where a record gives
a description of a place and not coordinates, the point is marked approximate and says why.
NOT_FILED is the list of filings looked for and not found, with the date of the last look.
"""

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
        "id": "tceq-TXR1535YT"}),
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
        "id": "tceq-air-184781"}),
    ((-96.15967, 30.65237), {
        "name": "River pump station tracts",
        "description": "Two tracts on the Navasota River, 3.83 and 1.25 acres, listed under WIT TECH LLC.",
        "Status": PURCHASE,
        "Listed under": "WIT TECH LLC",
        "Deed date": "May 27, 2026",
        "Improvements on the appraisal record": "None",
        "Water rights": "State records still list the river and reservoir water rights under the reservoir's prior owner as of October 6, 2026.",
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

# Filings looked for and not found. Remove a line when the filing appears, and add it to ITEMS.
NOT_FILED_AS_OF = date(2026, 10, 8)
NOT_FILED = [
    "an air permit for the on-site power plants",
    "a wastewater discharge permit",
    "a water right transfer or amendment",
    "a groundwater permit",
    "a federal (Army Corps) permit",
]


def not_filed_note():
    when = f"{NOT_FILED_AS_OF:%B} {NOT_FILED_AS_OF.day}, {NOT_FILED_AS_OF.year}"
    return f"Not filed yet. None found as of {when} for: " + "; ".join(NOT_FILED) + "."


def main():
    features = [{"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": list(point)}} for point, props in ITEMS]
    size = publish_features("filings", "Permits and filings", OUT, features, SOURCE, ("item", "items"), key="id", note=not_filed_note())
    print(f"Wrote {len(features)} items to {OUT.name} ({size / 1024:.1f} KB).")
    for f in features:
        print(f"  {f['properties']['Status']:34} {f['properties']['name']}")


if __name__ == "__main__":
    main()
