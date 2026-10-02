"""Pull water well records for Grimes County into data/water_wells.geojson.

Source: Texas Water Development Board, Submitted Drillers Reports: the report a driller files
with the state when a well is drilled. Coverage starts around 2001, so older wells are missing,
and locations are as accurate as the driller recorded them.

Only what describes the well is kept: its use, depth and completion year. The owner's name and
address, which the state record also holds, are never downloaded.

    python scripts/fetch_water_wells.py
"""

import sys
from datetime import datetime, timezone

import geopandas as gpd

from common import COUNTY_NAME, DATA, county_boundary, fetch_json, publish_geojson

SERVICE = "https://services.twdb.texas.gov/arcgis/rest/services/Public/WellReports/MapServer/0/query"
FIELDS = "WellReportTrackingNumber,WellType,ProposedUse,DateOfWellCompletion,BoreholeDepthFt,PluggingReportTrackingNumber"
PAGE_SIZE = 1000
OUT = DATA / "water_wells.geojson"

# The state's use categories, grouped for the map legend
GROUPS = {
    "Domestic": "Household",
    "Stock": "Livestock or irrigation", "Irrigation": "Livestock or irrigation",
    "Public Supply": "Public water supply",
    "Industrial": "Industrial or drilling supply", "Rig Supply": "Industrial or drilling supply",
    "Fracking Supply": "Industrial or drilling supply",
}
OTHER = "Monitoring, testing or other"


def download():
    features, offset = [], 0
    while True:
        page = fetch_json(SERVICE, {"where": f"County='{COUNTY_NAME}'", "outFields": FIELDS, "orderByFields": "ObjectId",
                                    "resultOffset": offset, "resultRecordCount": PAGE_SIZE, "outSR": 4326, "f": "geojson"},
                          timeout=300)["features"]
        features += page
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return gpd.GeoDataFrame.from_features(features, crs=4326) if features else gpd.GeoDataFrame()


def year(milliseconds):
    if milliseconds is None or milliseconds != milliseconds:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).year


def main():
    raw = download()
    if raw.empty:
        sys.exit("The Water Development Board returned no well reports for Grimes County. Nothing written.")
    raw = raw[raw.geometry.notna() & ~raw.geometry.is_empty]
    raw = gpd.clip(raw, county_boundary())       # a few reports are tagged Grimes but plotted elsewhere

    use = raw["ProposedUse"].fillna("Unknown").str.strip()
    group = use.map(lambda u: GROUPS.get(u, OTHER))
    depth = raw["BoreholeDepthFt"].where(raw["BoreholeDepthFt"] > 0)
    wells = gpd.GeoDataFrame({
        "name": group.map(lambda g: "Water well" if g != OTHER else "Well or boring") ,
        "description": use.map(lambda u: f"Recorded use: {u.lower()}"),
        "Use": group,
        "Depth (feet)": depth.round(),
        "Completed": raw["DateOfWellCompletion"].map(year),
        "Work": raw["WellType"].fillna("").str.strip().replace({"": None, "Unknown": None}),
        "Plugged": raw["PluggingReportTrackingNumber"].map(lambda p: "Yes" if p == p and p not in (None, 0, "") else None),
        "State report number": raw["WellReportTrackingNumber"].astype(str),
    }, geometry=raw.geometry, crs=4326).sort_values("State report number", key=lambda s: s.astype(int)).reset_index(drop=True)

    size = publish_geojson("wells", "Water wells", OUT, wells, SERVICE, ("well", "wells"), key="State report number")
    print(f"Downloaded {len(raw)} reports; wrote {len(wells)} wells to {OUT.name} ({size / 1e6:.2f} MB).")
    for name, n in wells["Use"].value_counts().items():
        print(f"  {n:>5}  {name}")
    deep = wells["Depth (feet)"].dropna()
    print(f"  depth: median {deep.median():.0f} feet, deepest {deep.max():.0f} feet; completed {int(wells['Completed'].min())} to "
          f"{int(wells['Completed'].max())}; {wells['Plugged'].notna().sum()} plugged")


if __name__ == "__main__":
    main()
