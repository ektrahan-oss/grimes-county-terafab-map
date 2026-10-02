"""Pull permitted wastewater discharge points near Grimes County into data/wastewater_outfalls.geojson.

Source: Texas Commission on Environmental Quality (TCEQ), Wastewater Outfalls. An outfall is the
point where a permitted facility releases treated wastewater into a stream. Covers the county plus
a 5 mile buffer, since discharges upstream of the county line flow into it.

The permit holder's name is kept as TCEQ records it. Holders are businesses, cities and agencies.

    python scripts/fetch_wastewater_outfalls.py
"""

import sys

import geopandas as gpd

from common import DATA, county_boundary, fetch_json, publish_geojson

SERVICE = "https://gisweb.tceq.texas.gov/arcgis/rest/services/Outfalls/Outfalls/MapServer/0/query"
BUFFER_MILES = 5
OUT = DATA / "wastewater_outfalls.geojson"

# TCEQ's one-letter codes. Only the two common ones are spelled out; the rest are shown as recorded.
TYPES = {"D": "Domestic wastewater (sewage)", "W": "Industrial wastewater"}
STATUSES = {"C": "Current permit", "P": "Pending application"}


def main():
    area = county_boundary(BUFFER_MILES)
    west, south, east, north = area.total_bounds
    features = fetch_json(SERVICE, {
        "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outSR": 4326, "f": "geojson",
        "outFields": "PERMIT_NUM,OUTFALL,PERMITTEE,STATUS,DTYPE,COUNTY,SEGMENT"}, timeout=300)["features"]
    if not features:
        sys.exit("TCEQ returned no wastewater outfalls for the Grimes County area. Nothing written.")
    raw = gpd.GeoDataFrame.from_features(features, crs=4326)
    raw = gpd.clip(raw, area)

    def text(value):
        return " ".join(str(value or "").split())

    kind = raw["DTYPE"].map(lambda t: TYPES.get(text(t).upper(), f"Other (TCEQ type {text(t) or 'not recorded'})"))
    outfalls = gpd.GeoDataFrame({
        "name": raw["PERMITTEE"].map(text),
        "description": "Permitted wastewater discharge point",
        "Type": kind,
        "Status": raw["STATUS"].map(lambda s: STATUSES.get(text(s).upper(), text(s) or "Not recorded")),
        "Permit": raw["PERMIT_NUM"].map(text),
        "Outfall": raw["OUTFALL"].map(text),
        "County": raw["COUNTY"].map(lambda c: text(c).title()),
        "TCEQ stream segment": raw["SEGMENT"].map(text),
        "id": raw["PERMIT_NUM"].map(text) + "/" + raw["OUTFALL"].map(text),
    }, geometry=raw.geometry, crs=4326).sort_values(["Permit", "Outfall"]).reset_index(drop=True)

    size = publish_geojson("outfalls", "Wastewater outfalls", OUT, outfalls, SERVICE, ("outfall", "outfalls"), key="id")
    print(f"Wrote {len(outfalls)} outfalls to {OUT.name} ({size / 1024:.0f} KB).")
    for (kind, status), n in outfalls.groupby(["Type", "Status"]).size().items():
        print(f"  {n:>3}  {kind}, {status.lower()}")
    in_county = (outfalls["County"] == "Grimes").sum()
    print(f"  {in_county} are in Grimes County; the rest are within {BUFFER_MILES} miles of it")


if __name__ == "__main__":
    main()
