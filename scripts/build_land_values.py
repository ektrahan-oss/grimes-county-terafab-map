"""Work out the median appraised land value per acre across Grimes County into data/land_values.geojson.

Source: TxGIO StratMap Land Parcels (public domain), compiled from the Grimes Central Appraisal
District. Each release carries the district's appraised land value for one tax year.

The county is covered with hexagons 3 miles across. Each parcel of an acre or more is counted in the
hexagon its middle falls in, and the hexagon shows the median value per acre of its parcels. Nothing
about a single property is written: a hexagon with fewer than 5 parcels shows no figure at all, and
owner names and addresses are never read from the file.

Each tax year's results are also saved under data/land_value_history/, so a later release can be
compared with this one.

Run by hand when TxGIO publishes a new release; the parcel download is refused from GitHub's servers:

    python scripts/build_land_values.py --refresh-parcels
"""

import json
import math
import sys
import zipfile

import geopandas as gpd
from shapely.geometry import Polygon

import build_reinvestment_zone as zone
from common import DATA, WORK_CRS, county_boundary, publish_features, _rounded

HEX_MILES = 3                    # across, flat side to flat side
MIN_PARCELS = 5                  # fewer than this in a hexagon and no figure is shown
MIN_ACRES = 1                    # smaller lots are priced as house sites, not as land by the acre
ROUND_TO = 10                    # dollars
OUT = DATA / "land_values.geojson"
HISTORY = DATA / "land_value_history"
SOURCE = "https://data.geographic.texas.gov/ (StratMap Land Parcels, Grimes County)"


def hexagons(area):
    """Hexagons covering a shape, on a grid fixed to the map's coordinates so it never shifts between years."""
    width = HEX_MILES * 5280
    radius = width / math.sqrt(3)
    rise = 1.5 * radius
    west, south, east, north = area.bounds
    cells = []
    for row in range(math.floor(south / rise) - 1, math.ceil(north / rise) + 2):
        shift = width / 2 if row % 2 else 0
        for col in range(math.floor((west - shift) / width) - 1, math.ceil((east - shift) / width) + 2):
            cx, cy = col * width + shift, row * rise
            cell = Polygon([(cx + radius * math.cos(math.radians(60 * k + 30)), cy + radius * math.sin(math.radians(60 * k + 30)))
                            for k in range(6)])
            if cell.intersects(area):
                cells.append({"id": f"r{row}c{col}", "geometry": cell})
    return gpd.GeoDataFrame(cells, crs=WORK_CRS)


def properties():
    """One row per property with its land value and mapped acres. Only those two columns and the ID are read."""
    zip_path, label = zone.fetch_parcels("--refresh-parcels" in sys.argv)
    shp = next(n for n in zipfile.ZipFile(zip_path).namelist() if n.lower().endswith(".shp"))
    parcels = gpd.read_file(f"zip://{zip_path}!{shp}", columns=["Prop_ID", "LAND_VALUE", "TAX_YEAR"]).to_crs(WORK_CRS)
    if "LAND_VALUE" not in parcels.columns:
        sys.exit(f"The parcel release {label} has no land value column. Nothing written.")
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty]
    parcels["Prop_ID"] = parcels["Prop_ID"].map(zone.norm_id)
    parcels = parcels[~parcels["Prop_ID"].isin(["", "0"])]            # road and water strips with no property record
    tax_year = int(parcels["TAX_YEAR"].mode().iloc[0])
    parcels["acres"] = parcels.area / 43560
    # A property drawn in several pieces repeats its value on each piece: add up the acres, count the value once
    acres = parcels.groupby("Prop_ID")["acres"].sum()
    one = parcels.sort_values("acres", ascending=False).drop_duplicates("Prop_ID").set_index("Prop_ID")
    one["acres"] = acres
    one["geometry"] = one.geometry.representative_point()             # a point inside the largest piece
    return one, tax_year, label


def main():
    county = county_boundary().to_crs(WORK_CRS).geometry.iloc[0]
    props, tax_year, label = properties()
    no_value = int((props["LAND_VALUE"].fillna(0) <= 0).sum())
    small = int(((props["acres"] < MIN_ACRES) & (props["LAND_VALUE"] > 0)).sum())
    used = props[(props["LAND_VALUE"] > 0) & (props["acres"] >= MIN_ACRES)].copy()
    used["per_acre"] = used["LAND_VALUE"] / used["acres"]

    cells = hexagons(county)
    placed = gpd.sjoin(used[["per_acre", "geometry"]], cells, predicate="within")
    stats = placed.groupby("id")["per_acre"].agg(["size", "median"])
    cells["geometry"] = cells.geometry.intersection(county)
    cells = cells[~cells.geometry.is_empty]

    HISTORY.mkdir(exist_ok=True)
    earlier = sorted(int(p.stem) for p in HISTORY.glob("*.json") if p.stem.isdigit() and int(p.stem) < tax_year)
    before = json.loads((HISTORY / f"{earlier[-1]}.json").read_text(encoding="utf-8"))["areas"] if earlier else {}

    features, saved = [], {}
    for cell in cells.to_crs(4326).itertuples():
        count = int(stats["size"].get(cell.id, 0))
        p = {"name": "Land value per acre", "Tax year": str(tax_year), "id": cell.id}
        if count >= MIN_PARCELS:
            median = int(round(stats["median"][cell.id] / ROUND_TO) * ROUND_TO)
            p["description"] = f"Median appraised land value: ${median:,} an acre"
            p["Median land value per acre ($)"] = median
            p["Parcels counted"] = count
            saved[cell.id] = {"median_per_acre": median, "parcels": count}
            if cell.id in before:
                was = before[cell.id]["median_per_acre"]
                p[f"Change since tax year {earlier[-1]}"] = f"{(median - was) / was:+.1%} (${median - was:+,} an acre)"
        else:
            p["description"] = f"Too few parcels to show (fewer than {MIN_PARCELS} of an acre or more)"
        geometry = cell.geometry.__geo_interface__
        features.append({"type": "Feature", "properties": p,
                         "geometry": {"type": geometry["type"], "coordinates": _rounded(geometry["coordinates"])}})
    features.sort(key=lambda f: f["properties"]["id"])
    county_median = int(round(used["per_acre"].median() / ROUND_TO) * ROUND_TO)

    (HISTORY / f"{tax_year}.json").write_text(json.dumps({
        "tax_year": tax_year, "release": label, "hexagon_miles": HEX_MILES, "minimum_parcels": MIN_PARCELS,
        "county_median_per_acre": county_median, "parcels_counted": len(used), "areas": saved}, indent=1) + "\n", encoding="utf-8")

    shown = len(saved)
    note = (f"Tax year {tax_year}. The county-wide median is ${county_median:,} an acre, from {len(used):,} parcels; "
            f"{small:,} parcels under {MIN_ACRES} acre are left out.")
    size = publish_features("land", "Land value per acre", OUT, features, SOURCE, ("area", "areas"), key="id", note=note)
    print(f"Tax year {tax_year}, release {label}. Wrote {len(features)} hexagons to {OUT.name} ({size / 1024:.0f} KB): "
          f"{shown} with a figure, {len(features) - shown} with too few parcels.")
    print(f"  {len(props):,} properties; {no_value:,} with no land value and {small:,} under {MIN_ACRES} acre left out; {len(used):,} counted "
          f"({len(used) - len(placed):,} of those fell outside every hexagon)")
    print(f"  County-wide median ${county_median:,} an acre. Hexagon medians run from ${min(a['median_per_acre'] for a in saved.values()):,} "
          f"to ${max(a['median_per_acre'] for a in saved.values()):,}; parcels per shown hexagon from "
          f"{min(a['parcels'] for a in saved.values())} to {max(a['parcels'] for a in saved.values())}")
    print(f"  Saved {HISTORY.name}/{tax_year}.json ({(HISTORY / f'{tax_year}.json').stat().st_size / 1024:.0f} KB)"
          + (f"; compared with tax year {earlier[-1]}" if earlier else "; no earlier year to compare with yet"))


if __name__ == "__main__":
    main()
