"""Pull pipelines in Grimes County into data/pipelines.geojson.

Source: Railroad Commission of Texas public GIS viewer, Pipelines layer. It
covers the pipelines the Commission regulates: gathering and transmission lines
for natural gas, crude oil, refined products and highly volatile liquids.
Locations are the Commission's mapped approximations, not surveyed.

    python scripts/fetch_pipelines.py
"""

import sys

import geopandas as gpd

from common import COUNTY_NAME, DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = "https://gis.rrc.texas.gov/server/rest/services/rrc_public/RRC_Public_Viewer_Srvs/MapServer"
PAGE_SIZE = 500
SIMPLIFY_FEET = 15
OUT = DATA / "pipelines.geojson"

COMMODITIES = {
    "NATURAL GAS": "Natural gas",
    "CRUDE OIL": "Crude oil",
    "REFINED LIQUID PRODUCT": "Refined products",
    "HIGHLY VOLATILE LIQUID": "Highly volatile liquids (such as propane or ethane)",
    "HIGHLY VOLATILE LIQUID (HVL)": "Highly volatile liquids (such as propane or ethane)",
    "CARBON DIOXIDE": "Carbon dioxide",
}
STATUSES = {"Revoke": "Permit revoked"}


def layer_url():
    """Two layers are named Pipelines; the full one is the one not described as transmission only."""
    for layer in fetch_json(SERVICE, {"f": "json"})["layers"]:
        if layer["name"] == "Pipelines":
            if "transmission only" not in fetch_json(f"{SERVICE}/{layer['id']}", {"f": "json"}).get("description", "").lower():
                return f"{SERVICE}/{layer['id']}/query"
    sys.exit("The Railroad Commission service no longer has a full Pipelines layer. Nothing written.")


def download(url):
    features, offset = [], 0
    while True:
        page = fetch_json(url, {"where": f"COUNTY_NAME='{COUNTY_NAME.upper()}'",
                          "outFields": "OPERATOR,COMMODITY_DESCRIPTION,SYSTEM_TYPE,STATUS,DIAMETER",
                          "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
                          "outSR": 4326, "f": "geojson"}, timeout=300)["features"]
        features += page
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return gpd.GeoDataFrame.from_features(features, crs=4326) if features else gpd.GeoDataFrame()


def tidy(text):
    return " ".join(str(text or "").split())


def main():
    url = layer_url()
    raw = download(url)
    if raw.empty:
        sys.exit("The Railroad Commission returned no pipelines for Grimes County. Nothing written.")

    commodity = raw["COMMODITY_DESCRIPTION"].map(lambda c: COMMODITIES.get(tidy(c).upper(), tidy(c).capitalize()))
    kind = raw["SYSTEM_TYPE"].map(lambda t: "Gathering" if "gathering" in tidy(t).lower() else "Transmission")
    lines = gpd.GeoDataFrame({
        "name": commodity.str.split(" (", regex=False).str[0] + " pipeline",
        "Operator": raw["OPERATOR"].map(tidy),     # kept exactly as the Commission records it
        "Commodity": commodity,
        "Type": kind,
        "Status": raw["STATUS"].map(lambda s: STATUSES.get(tidy(s), tidy(s))),
        "Diameter (inches)": raw["DIAMETER"].where(raw["DIAMETER"] > 0),
    }, geometry=raw.geometry, crs=4326)

    lines = gpd.clip(lines, county_boundary()).to_crs(WORK_CRS)
    lines = lines[lines.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    lines["geometry"] = lines.geometry.simplify(SIMPLIFY_FEET)
    lines = lines.sort_values(["Commodity", "Operator", "Type"]).reset_index(drop=True)

    size = publish_geojson("pipelines", "Pipelines", OUT, lines, url, ("segment", "segments"))
    miles = lines.length / 5280
    print(f"Downloaded {len(raw)} segments; wrote {len(lines)} to {OUT.name} ({size / 1e6:.2f} MB, {miles.sum():,.0f} miles).")
    summary = miles.groupby([lines["Commodity"], lines["Type"], lines["Status"]]).agg(["count", "sum"])
    for (c, t, s), row in summary.iterrows():
        print(f"  {int(row['count']):>4} segments, {row['sum']:>6.1f} miles  {c}, {t.lower()}, {s.lower()}")
    print(f"  {lines['Operator'].nunique()} operators; {lines['Diameter (inches)'].notna().sum()} segments have a diameter")


if __name__ == "__main__":
    main()
