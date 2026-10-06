"""Inputs the analysis borrows from the map's own scripts, plus the one extra download it needs (state roads)."""

import importlib.util
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from common import CACHE, ROOT

# The map's helper module is also called "common": load it under its own name
_spec = importlib.util.spec_from_file_location("map_common", ROOT / "scripts" / "common.py")
map_common = importlib.util.module_from_spec(_spec)
sys.modules["map_common"] = map_common
_spec.loader.exec_module(map_common)

PARCEL_ZIP = CACHE / "stratmap26-landparcels_48185_lp.zip"     # the 2026 state parcel release, as of March 2026
ROADS = "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/TxDOT_Roadways/FeatureServer/0/query"


def read_parcels(columns, ignore_geometry=False):
    if not PARCEL_ZIP.exists():
        raise SystemExit(f"{PARCEL_ZIP.name} is not in .cache. Run scripts/build_parcel_tiles.py once to download it.")
    return map_common.read_parcel_file(PARCEL_ZIP, columns, ignore_geometry=ignore_geometry)


def fetch_roads(area, prefixes, pad_miles=0):
    """TxDOT's on-system road centerlines inside an area's bounding box, for the given route prefixes."""
    box = area.to_crs(4326).total_bounds
    pad = pad_miles / 60
    envelope = f"{box[0] - pad},{box[1] - pad},{box[2] + pad},{box[3] + pad}"
    where = "RTE_PRFX IN (" + ",".join(f"'{p}'" for p in prefixes) + ")"
    features, offset = [], 0
    while True:
        page = map_common.fetch_json(ROADS, {
            "f": "geojson", "where": where, "geometry": envelope, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "RTE_NM,RTE_PRFX,RTE_NBR,RDBD_TYPE,MAP_LBL", "outSR": 4326,
            "resultOffset": offset, "resultRecordCount": 1000})
        features += page["features"]
        if len(page["features"]) < 1000:
            break
        offset += 1000
    if not features:
        raise SystemExit("TxDOT returned no roads for the area. Nothing written.")
    return gpd.GeoDataFrame(pd.DataFrame([f["properties"] for f in features]),
                            geometry=[shape(f["geometry"]) for f in features], crs=4326)
