"""Pull the school districts covering Grimes County into data/school_districts.geojson.

Source: U.S. Census Bureau TIGERweb, current Unified School Districts layer.
Districts are cut to the county line, so one that mostly lies in a neighboring
county shows only its Grimes County part.

    python scripts/fetch_school_districts.py
"""

import sys

import geopandas as gpd
import shapely

from common import DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer"
LAYER_NAME = "Unified School Districts"
MIN_SQ_MILES = 0.25              # ignore neighbors that only brush the county line
SIMPLIFY_FEET = 30
OUT = DATA / "school_districts.geojson"

SHORT = [("Consolidated Independent School District", "CISD"), ("Independent School District", "ISD")]


def short_name(name):
    for long, short in SHORT:
        name = name.replace(long, short)
    return name


def layer_url():
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"] == LAYER_NAME:
            return f"{SERVICE}/{layer['id']}/query"
    sys.exit(f"TIGERweb no longer has a layer named {LAYER_NAME}. Nothing written.")


def main():
    county = county_boundary()
    west, south, east, north = county.total_bounds
    url = layer_url()
    features = fetch_json(url, {
        "where": "STATE='48'", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "NAME", "outSR": 4326,
        "f": "geojson"}, timeout=300)["features"]
    if not features:
        sys.exit("Census returned no school districts for the Grimes County area. Nothing written.")
    nearby = gpd.GeoDataFrame.from_features(features, crs=4326)

    districts = gpd.clip(nearby, county).to_crs(WORK_CRS)
    districts["sq_miles"] = districts.area / 5280 ** 2
    districts = districts[districts["sq_miles"] >= MIN_SQ_MILES].copy()
    # Simplify all districts together so shared borders stay shared, with no gaps or overlaps
    districts["geometry"] = shapely.coverage_simplify(districts.geometry.values, SIMPLIFY_FEET)
    districts["name"] = districts["NAME"].map(short_name)
    districts = districts.rename(columns={"NAME": "description"}).sort_values("sq_miles", ascending=False)

    size = publish_geojson("schools", "School districts", OUT, districts[["name", "description", "geometry"]], url,
                           ("district", "districts"), key="name")
    print(f"{len(nearby)} districts near the county; wrote {len(districts)} to {OUT.name} ({size / 1024:.0f} KB).")
    for _, d in districts.iterrows():
        print(f"  {d['sq_miles']:>6.1f} sq miles in Grimes County  {d['name']}")
    print(f"  {districts['sq_miles'].sum():.1f} of the county's {county.to_crs(WORK_CRS).area.iloc[0] / 5280 ** 2:.1f} sq miles covered")


if __name__ == "__main__":
    main()
