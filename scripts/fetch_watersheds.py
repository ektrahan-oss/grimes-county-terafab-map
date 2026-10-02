"""Pull the watersheds covering Grimes County into data/watersheds.geojson.

Source: U.S. Geological Survey Watershed Boundary Dataset, 10-digit watersheds. A watershed is
the land that drains to one stream, so these show which way water leaving any spot will run.
Watersheds are cut to the county line.

    python scripts/fetch_watersheds.py
"""

import sys

import geopandas as gpd
import shapely

from common import DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer"
LAYER_NAME = "10-digit HU (Watershed)"
MIN_SQ_MILES = 1                 # ignore neighbors that only brush the county line
SIMPLIFY_FEET = 60
OUT = DATA / "watersheds.geojson"


def layer_url():
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"] == LAYER_NAME:
            return f"{SERVICE}/{layer['id']}/query"
    sys.exit(f"The USGS service no longer has a layer named {LAYER_NAME}. Nothing written.")


def main():
    county = county_boundary()
    west, south, east, north = county.total_bounds
    url = layer_url()
    features = fetch_json(url, {
        "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "name,huc10", "outSR": 4326,
        "f": "geojson"}, timeout=300)["features"]
    if not features:
        sys.exit("USGS returned no watersheds for the Grimes County area. Nothing written.")
    nearby = gpd.GeoDataFrame.from_features(features, crs=4326)

    sheds = gpd.clip(nearby, county).to_crs(WORK_CRS)
    sheds = sheds.dissolve(by=["name", "huc10"], as_index=False)
    sheds["sq_miles"] = sheds.area / 5280 ** 2
    sheds = sheds[sheds["sq_miles"] >= MIN_SQ_MILES].copy()
    # Simplify all watersheds together so shared borders stay shared
    sheds["geometry"] = shapely.coverage_simplify(sheds.geometry.values, SIMPLIFY_FEET)
    sheds["description"] = "Watershed: the land draining to this stream"
    sheds = sheds.rename(columns={"huc10": "USGS watershed code"}).sort_values("sq_miles", ascending=False)

    size = publish_geojson("watersheds", "Watersheds", OUT, sheds[["name", "description", "USGS watershed code", "geometry"]], url,
                           ("watershed", "watersheds"), key="USGS watershed code")
    print(f"{len(nearby)} watersheds near the county; wrote {len(sheds)} to {OUT.name} ({size / 1024:.0f} KB).")
    for _, s in sheds.iterrows():
        print(f"  {s['sq_miles']:>6.1f} sq miles in Grimes County  {s['name']}")


if __name__ == "__main__":
    main()
