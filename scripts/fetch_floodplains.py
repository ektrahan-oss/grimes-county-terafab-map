"""Pull FEMA flood zones for Grimes County into data/floodplains.geojson.

Source: FEMA National Flood Hazard Layer (NFHL), Flood Hazard Zones. Only the
mapped flood areas are kept: the 1% annual chance (100-year) zones, floodways,
and the 0.2% annual chance (500-year) zone. "Area of minimal flood hazard",
which covers the rest of the county, is left out.

    python scripts/fetch_floodplains.py
"""

import io
import sys

import geopandas as gpd
import pandas as pd

from common import COUNTY_FIPS, DATA, WORK_CRS, county_boundary, fetch, fetch_json, publish_geojson

SERVICE = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
LAYER_NAME = "Flood Hazard Zones"
BATCH = 10                       # polygons per request; some are very detailed
SIMPLIFY_FEET = 15
MIN_ACRES = 0.25                 # drop slivers left over from dissolving and clipping
OUT = DATA / "floodplains.geojson"

FLOODWAY = "Floodway (channel that must stay clear to carry the 100-year flood)"
LABELS = {
    "A": "1% annual chance flood (100-year), approximate study",
    "AE": "1% annual chance flood (100-year)",
    "AH": "1% annual chance flood (100-year), shallow ponding",
    "AO": "1% annual chance flood (100-year), shallow sheet flow",
    "A99": "1% annual chance flood (100-year), levee system under construction",
    "AR": "1% annual chance flood (100-year), levee being restored",
    "V": "1% annual chance coastal flood (100-year) with wave action",
    "VE": "1% annual chance coastal flood (100-year) with wave action",
    "D": "Flood risk not determined",
}
SUBTYPE_LABELS = {
    "0.2 PCT ANNUAL CHANCE FLOOD HAZARD": "0.2% annual chance flood (500-year)",
    "AREA WITH REDUCED FLOOD RISK DUE TO LEVEE": "Reduced flood risk due to levee",
}
NOT_MAPPED = {"AREA OF MINIMAL FLOOD HAZARD", "AREA NOT INCLUDED", "OPEN WATER"}


def label(zone, subtype):
    if "FLOODWAY" in subtype:
        return FLOODWAY
    if subtype in SUBTYPE_LABELS:
        return SUBTYPE_LABELS[subtype]
    return LABELS.get(zone, f"Flood zone {zone}")


def layer_url():
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"] == LAYER_NAME:
            return f"{SERVICE}/{layer['id']}/query"
    sys.exit(f"FEMA's NFHL service no longer has a layer named {LAYER_NAME}. Nothing written.")


def download(url):
    where = f"DFIRM_ID='{COUNTY_FIPS}C'"
    ids = fetch_json(url, {"where": where, "returnIdsOnly": "true", "f": "json"}).get("objectIds") or []
    pages = []
    for i in range(0, len(ids), BATCH):
        raw = fetch(url, {"objectIds": ",".join(map(str, ids[i:i + BATCH])), "outFields": "FLD_ZONE,ZONE_SUBTY",
                          "outSR": 4326, "f": "geojson"}, timeout=300)
        pages.append(gpd.read_file(io.BytesIO(raw)))
    return pd.concat(pages, ignore_index=True) if pages else gpd.GeoDataFrame()


def main():
    url = layer_url()
    zones = download(url)
    if zones.empty:
        sys.exit("FEMA returned no flood zones for Grimes County. Nothing written.")
    downloaded = len(zones)

    zones["ZONE_SUBTY"] = zones["ZONE_SUBTY"].fillna("").str.strip().str.upper()
    zones = zones[~zones["ZONE_SUBTY"].isin(NOT_MAPPED)].copy()
    zones["name"] = [label(z, s) for z, s in zip(zones["FLD_ZONE"], zones["ZONE_SUBTY"])]
    zones = zones.rename(columns={"FLD_ZONE": "Flood zone"})[["name", "Flood zone", "geometry"]]

    # Merge pieces FEMA split at map panel edges, then cut back into separate areas
    zones["geometry"] = zones.geometry.make_valid()
    zones = gpd.clip(zones, county_boundary()).to_crs(WORK_CRS)
    zones = zones.dissolve(by=["name", "Flood zone"], as_index=False).explode(ignore_index=True)
    zones = zones[zones.geometry.geom_type == "Polygon"]
    zones["geometry"] = zones.geometry.simplify(SIMPLIFY_FEET, preserve_topology=True)
    zones = zones[zones.area >= MIN_ACRES * 43560]

    size = publish_geojson("flood", "Floodplains", OUT, zones, url, ("flood area", "flood areas"))
    print(f"Downloaded {downloaded} FEMA polygons; wrote {len(zones)} flood areas to {OUT.name} ({size / 1e6:.2f} MB).")
    acres = (zones.area / 43560).groupby(zones["name"]).agg(["count", "sum"]).round(0)
    for name, row in acres.iterrows():
        print(f"  {int(row['count']):>4} areas, {int(row['sum']):>7,} acres  {name}")


if __name__ == "__main__":
    main()
