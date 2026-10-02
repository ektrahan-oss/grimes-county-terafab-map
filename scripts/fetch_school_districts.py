"""Pull the school districts covering Grimes County into data/school_districts.geojson.

Source: U.S. Census Bureau TIGERweb, current Unified School Districts layer.
Districts are cut to the county line, so one that mostly lies in a neighboring
county shows only its Grimes County part.

Each district also carries its enrollment by school year from the Texas Education
Agency (see school_enrollment.py), shown in the district's pop-up.

    python scripts/fetch_school_districts.py
"""

import json
import sys

import geopandas as gpd
import shapely

from common import DATA, WORK_CRS, _rounded, county_boundary, fetch_json, publish_features, record_layer
from school_enrollment import FORM as ENROLLMENT_FORM, change, enrollment

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

    features = json.loads(districts[["name", "description", "geometry"]].to_crs(4326).to_json(drop_id=True))["features"]
    history = enrollment([f["properties"]["name"] for f in features])
    for f in features:
        f["geometry"]["coordinates"] = _rounded(f["geometry"]["coordinates"])
        years = history[f["properties"]["name"]]
        first, latest = min(years), max(years)
        added = {
            f"Students enrolled, {latest}": years[latest],
            "Change in one year": change(years, 1),
            "Change in five years": change(years, 5),
            f"Change since {first}": change(years, len(years) - 1),
            "Enrollment by year": years,         # drawn as a small chart in the pop-up
            "Enrollment covers": "The whole district, including any part outside Grimes County",
            "url": ENROLLMENT_FORM,
            "link": "Texas Education Agency enrollment reports",
        }
        f["properties"].update({k: v for k, v in added.items() if v is not None})

    size = publish_features("schools", "School districts", OUT, features, url, ("district", "districts"), key="name")
    record_layer("enrollment", ENROLLMENT_FORM, len(features))
    print(f"{len(nearby)} districts near the county; wrote {len(districts)} to {OUT.name} ({size / 1024:.0f} KB).")
    for (_, d), f in zip(districts.iterrows(), features):
        years = f["properties"]["Enrollment by year"]
        print(f"  {d['sq_miles']:>6.1f} sq miles in Grimes County  {d['name']:<20} {min(years)} to {max(years)} ({len(years)} years): "
              f"{years[min(years)]:,} to {years[max(years)]:,} students, one year {change(years, 1)}, five years {change(years, 5)}")
    print(f"  {districts['sq_miles'].sum():.1f} of the county's {county.to_crs(WORK_CRS).area.iloc[0] / 5280 ** 2:.1f} sq miles covered")


if __name__ == "__main__":
    main()
