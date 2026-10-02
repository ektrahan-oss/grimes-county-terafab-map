"""Pull electric transmission lines near Grimes County into data/power_lines.geojson.

Source: U.S. Electric Power Transmission Lines, the federal HIFLD dataset.
HIFLD's open data site was shut down in 2025 and this layer is now an archive:
its last data update was September 30, 2024 and it will not be updated again.
Lines built since then are not in it.

Planned: around April 2027 (or sooner if asked), switch the line shapes to
OpenStreetMap power=line via the Overpass API so new construction shows up, and
fill any missing voltage or owner by spatial join against this federal data.

Covers the county plus a 5 mile buffer. Transmission lines only (69 kV and up),
not the local distribution lines along roads.

    python scripts/fetch_power_lines.py
"""

import sys

import geopandas as gpd

from common import DATA, WORK_CRS, county_boundary, fetch_json, publish_geojson

SERVICE = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
           "US_Electric_Power_Transmission_Lines/FeatureServer/0/query")
BUFFER_MILES = 5
PAGE_SIZE = 1000
SIMPLIFY_FEET = 15
OUT = DATA / "power_lines.geojson"

UNKNOWN = {"", "NOT AVAILABLE", "UNKNOWN", "N/A"}


def download(area):
    west, south, east, north = area.total_bounds
    features, offset = [], 0
    while True:
        page = fetch_json(SERVICE, {
            "where": "1=1", "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "OWNER,VOLTAGE,TYPE",
            "orderByFields": "OBJECTID_1", "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
            "outSR": 4326, "f": "geojson"}, timeout=300)["features"]
        features += page
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return gpd.GeoDataFrame.from_features(features, crs=4326) if features else gpd.GeoDataFrame()


def known(text):
    text = " ".join(str(text or "").split())
    return None if text.upper() in UNKNOWN else text


def main():
    area = county_boundary(BUFFER_MILES)
    raw = download(area)
    if raw.empty:
        sys.exit("No transmission lines returned for the Grimes County area. Nothing written.")

    voltage = raw["VOLTAGE"].where(raw["VOLTAGE"] > 0)         # the source uses -999999 for unknown
    lines = gpd.GeoDataFrame({
        "name": [f"{v:g} kV transmission line" if v == v else "Transmission line" for v in voltage],
        "Voltage (kV)": voltage,
        "Owner": raw["OWNER"].map(known),                      # utility company, as the source records it
        "Type": raw["TYPE"].map(lambda t: "Underground" if "UNDERGROUND" in str(t).upper() else None),
    }, geometry=raw.geometry, crs=4326)

    lines = gpd.clip(lines, area).to_crs(WORK_CRS)
    lines = lines[lines.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    lines["geometry"] = lines.geometry.simplify(SIMPLIFY_FEET)
    # A stable sort from a fixed starting order, so the file comes out the same on every machine
    lines = lines.sort_index().sort_values(["Voltage (kV)", "Owner"], ascending=[False, True], na_position="last",
                                           kind="stable").reset_index(drop=True)

    size = publish_geojson("power", "Power lines", OUT, lines, SERVICE, ("line", "lines"))
    miles = lines.length / 5280
    print(f"Downloaded {len(raw)} lines; wrote {len(lines)} to {OUT.name} ({size / 1024:.0f} KB, {miles.sum():,.0f} miles).")
    by_voltage = miles.groupby(lines["name"]).agg(["count", "sum"]).sort_values("sum", ascending=False)
    for name, row in by_voltage.iterrows():
        print(f"  {int(row['count']):>3} lines, {row['sum']:>6.1f} miles  {name}")
    in_county = gpd.clip(lines.to_crs(4326), county_boundary())
    print(f"  {len(in_county)} of them cross Grimes County itself; {lines['Owner'].notna().sum()} have a named owner")
    for owner, n in lines["Owner"].fillna("(not recorded)").value_counts().items():
        print(f"    {n:>3}  {owner}")


if __name__ == "__main__":
    main()
