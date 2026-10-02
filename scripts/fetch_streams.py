"""Pull the named streams of Grimes County into data/streams.geojson.

Source: U.S. Geological Survey National Hydrography Dataset, large-scale flowlines. Only streams
with a name are kept; the thousands of unnamed draws and ditches are left out.

    python scripts/fetch_streams.py
"""

import sys

import geopandas as gpd

from common import DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"
LAYER_NAME = "Flowline - Large Scale"
PAGE_SIZE = 1000
SIMPLIFY_FEET = 20
MAIN_RIVERS = ("River",)         # names ending this way are drawn heavier on the map
OUT = DATA / "streams.geojson"


def layer_url():
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"].strip() == LAYER_NAME:
            return f"{SERVICE}/{layer['id']}/query"
    sys.exit(f"The USGS service no longer has a layer named {LAYER_NAME}. Nothing written.")


def download(url, bounds):
    west, south, east, north = bounds
    features, offset = [], 0
    while True:
        page = fetch_json(url, {
            "where": "gnis_name IS NOT NULL", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "gnis_name", "orderByFields": "OBJECTID",
            "resultOffset": offset, "resultRecordCount": PAGE_SIZE, "outSR": 4326, "f": "geojson"}, timeout=300)["features"]
        features += page
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return gpd.GeoDataFrame.from_features(features, crs=4326) if features else gpd.GeoDataFrame()


def main():
    county = county_boundary()
    url = layer_url()
    raw = download(url, county.total_bounds)
    if raw.empty:
        sys.exit("USGS returned no named streams for Grimes County. Nothing written.")

    streams = gpd.clip(raw.rename(columns={"gnis_name": "name"}), county).to_crs(WORK_CRS)
    streams = streams[streams.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    streams = streams.dissolve(by="name", as_index=False)      # one feature per stream
    streams["geometry"] = streams.geometry.line_merge().simplify(SIMPLIFY_FEET)
    streams["miles"] = streams.length / 5280
    streams["Type"] = ["River" if n.endswith(MAIN_RIVERS) else "Creek or other stream" for n in streams["name"]]
    streams = streams.sort_values("name").reset_index(drop=True)

    size = publish_geojson("streams", "Streams", OUT, streams[["name", "Type", "geometry"]], url, ("stream", "streams"), key="name")
    print(f"Downloaded {len(raw)} named stream segments; wrote {len(streams)} streams to {OUT.name} "
          f"({size / 1024:.0f} KB, {streams['miles'].sum():,.0f} miles).")
    for _, s in streams.sort_values("miles", ascending=False).head(8).iterrows():
        print(f"  {s['miles']:>6.1f} miles in the county  {s['name']}")


if __name__ == "__main__":
    main()
