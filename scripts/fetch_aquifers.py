"""Pull the aquifers under Grimes County into data/aquifers.geojson.

Source: Texas Water Development Board, major and minor aquifers. Each shape is the mapped extent
of an aquifer, cut to the county line. Aquifers lie at different depths, so their extents overlap.

Groundwater in Grimes County is regulated by the Bluebonnet Groundwater Conservation District,
which covers the whole county, so it is named in the map legend and not drawn as its own layer.

    python scripts/fetch_aquifers.py
"""

import sys

import geopandas as gpd
import pandas as pd

from common import DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = "https://services.twdb.texas.gov/arcgis/rest/services/Base/BaseLayerQueryService/MapServer"
LAYERS = {"Major Aquifers": "Major aquifer", "Minor Aquifers": "Minor aquifer"}
SIMPLIFY_FEET = 60
OUT = DATA / "aquifers.geojson"


def main():
    county = county_boundary()
    west, south, east, north = county.total_bounds
    ids = {layer["name"]: layer["id"] for layer in fetch_json(SERVICE, {"f": "json"})["layers"]}
    parts = []
    for layer_name, rank in LAYERS.items():
        if layer_name not in ids:
            sys.exit(f"The Water Development Board service no longer has a layer named {layer_name}. Nothing written.")
        features = fetch_json(f"{SERVICE}/{ids[layer_name]}/query", {
            "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "AquiferName", "outSR": 4326,
            "f": "geojson"}, timeout=300)["features"]
        if features:
            part = gpd.GeoDataFrame.from_features(features, crs=4326)
            part["Rank"] = rank
            parts.append(part)
    if not parts:
        sys.exit("No aquifers returned for Grimes County. Nothing written.")

    aquifers = gpd.clip(pd.concat(parts, ignore_index=True), county).to_crs(WORK_CRS)
    aquifers = aquifers[aquifers.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    aquifers = aquifers.dissolve(by=["AquiferName", "Rank"], as_index=False)
    aquifers["geometry"] = aquifers.geometry.simplify(SIMPLIFY_FEET)
    aquifers["sq_miles"] = aquifers.area / 5280 ** 2
    aquifers["name"] = aquifers["AquiferName"] + " Aquifer"
    aquifers["description"] = "Mapped extent of the aquifer within Grimes County"
    aquifers = aquifers.sort_values("sq_miles", ascending=False)

    size = publish_geojson("aquifers", "Aquifers", OUT, aquifers[["name", "description", "Rank", "geometry"]],
                           f"{SERVICE}/{ids['Major Aquifers']}/query", ("aquifer", "aquifers"), key="name")
    print(f"Wrote {len(aquifers)} aquifers to {OUT.name} ({size / 1024:.0f} KB).")
    for _, a in aquifers.iterrows():
        print(f"  {a['sq_miles']:>6.1f} sq miles in Grimes County  {a['name']} ({a['Rank'].lower()})")


if __name__ == "__main__":
    main()
