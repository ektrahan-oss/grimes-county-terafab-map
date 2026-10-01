"""Pull the Grimes County boundary from Census TIGER into data/grimes_county.geojson.

Source: U.S. Census Bureau TIGERweb, current Counties layer. Other layer scripts
clip to this boundary, so run this one first on a fresh checkout.

    python scripts/fetch_county_boundary.py
"""

import io
import sys

import geopandas as gpd

from common import COUNTY_FIPS, DATA, fetch, fetch_json, record_layer, write_geojson

SERVICE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer"
SIMPLIFY_DEGREES = 0.0001        # about 35 feet; the rivers on the county line stay recognisable
OUT = DATA / "grimes_county.geojson"


def counties_layer_id():
    """The layer number changes between TIGERweb releases, so look it up by name."""
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"] == "Counties":
            return layer["id"]
    sys.exit("TIGERweb no longer has a layer named Counties. Nothing written.")


def main():
    url = f"{SERVICE}/{counties_layer_id()}/query"
    raw = fetch(url, {"where": f"GEOID='{COUNTY_FIPS}'", "outFields": "NAME", "outSR": 4326, "f": "geojson"})
    county = gpd.read_file(io.BytesIO(raw))
    if len(county) != 1:
        sys.exit(f"Expected one county for FIPS {COUNTY_FIPS}, got {len(county)}. Nothing written.")

    county = county.rename(columns={"NAME": "name"})[["name", "geometry"]]
    county["description"] = "County boundary, U.S. Census Bureau"
    county["geometry"] = county.geometry.simplify(SIMPLIFY_DEGREES, preserve_topology=True)

    size = write_geojson(OUT, county)
    record_layer("county", url, len(county))
    points = len(county.geometry.iloc[0].exterior.coords)
    print(f"Wrote {OUT.name}: {county['name'].iloc[0]}, {points:,} points, {size / 1024:.0f} KB.")


if __name__ == "__main__":
    main()
