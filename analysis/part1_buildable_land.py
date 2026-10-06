"""Part 1: Buildable land in Grimes County.

Starts with every parcel in the county, takes out land that is committed or constrained, and
adds up what is left by drive time from the site, by size, and by road frontage.

"Buildable" here is a first screen, not a judgment about any property. It does not look at slope,
wetlands, soils, existing homes, deed restrictions, or whether the owner would ever sell.

Public result:   analysis/outputs/part1_buildable_land.csv   (totals only, no parcel IDs)
Private result:  analysis/private/part1_parcels.gpkg         (parcel-level, for Parts 2 and 10)

    python analysis/part1_buildable_land.py
"""

import json
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from common import (BEYOND, CACHE, DATA, OUTPUTS, PRIVATE, RING_ORDER, RINGS, SQFT_PER_ACRE, WORK_CRS,
                    layer_date, save_key_numbers)
from scripts_bridge import fetch_roads, read_parcels

warnings.filterwarnings("ignore", message=".*geographic CRS.*")

# ---------------------------------------------------------------------------------------------
# SETTINGS: every assumption in this part
# ---------------------------------------------------------------------------------------------
# Buffers, in feet on each side of the line. Three scenarios are run; "middle" is the headline.
SCENARIOS = {
    "low":    {"pipeline_ft": 25,  "power_ft": 50,  "note": "narrow buffers, so more land counts as buildable"},
    "middle": {"pipeline_ft": 50,  "power_ft": 100, "note": "the plan's default buffers"},
    "high":   {"pipeline_ft": 100, "power_ft": 150, "note": "wide buffers, so less land counts as buildable"},
}
PIPELINE_STATUSES = ["In Service"]            # abandoned and revoked lines are left out
FLOOD_ZONES_REMOVED = ["A", "AE"]             # floodway and 100-year floodplain; the 500-year area (X) stays in
MIN_REMAINDER_ACRES = 0.1                     # a leftover smaller than this is a sliver, not a site
SIZE_CLASSES = [(0, 5, "Under 5 acres"), (5, 20, "5 to 20 acres"), (20, 100, "20 to 100 acres"), (100, 1e9, "100 acres or more")]
ROAD_PREFIXES = ["SH", "FM", "US", "BS", "SL", "SS"]   # state highways, farm-to-market roads and their business routes, loops and spurs
FRONTAGE_FT = 150                             # a parcel this close to the road's centerline counts as touching it
FRONTAGE_FT_RANGE = (100, 200)                # reported as a low and high check
OVERLAP_MIN_SQFT = 500                        # parcels overlapping by less than this are treated as neighbors, not stacked
# ---------------------------------------------------------------------------------------------

SIZE_ORDER = [label for _, _, label in SIZE_CLASSES]
GRID_FT = 5000                                # big shapes are cut into squares this size so the subtraction runs quickly


def acres(geoms):
    return shapely.area(np.asarray(geoms)) / SQFT_PER_ACRE


def in_pieces(shapes):
    """Cut a set of polygons into grid squares, so each later subtraction only touches nearby pieces."""
    whole = shapely.union_all(np.asarray(shapes))
    if whole.is_empty:
        return gpd.GeoSeries([], crs=WORK_CRS)
    x0, y0, x1, y1 = whole.bounds
    cells = [shapely.box(x, y, x + GRID_FT, y + GRID_FT)
             for x in np.arange(x0, x1, GRID_FT) for y in np.arange(y0, y1, GRID_FT)]
    cut = shapely.intersection(np.asarray(cells), whole)
    cut = cut[~shapely.is_empty(cut)]
    return gpd.GeoSeries(cut, crs=WORK_CRS)


def subtract(geoms, pieces):
    """Each geometry with the pieces removed."""
    geoms = np.asarray(geoms).copy()
    if len(pieces) == 0:
        return geoms
    left, right = pieces.sindex.query(geoms, predicate="intersects")
    if len(left) == 0:
        return geoms
    order = np.argsort(left, kind="stable")
    left, right = left[order], right[order]
    starts = np.r_[0, np.flatnonzero(np.diff(left)) + 1]
    piece_array = np.asarray(pieces)
    for start, end in zip(starts, np.r_[starts[1:], len(left)]):
        i = left[start]
        geoms[i] = shapely.difference(geoms[i], shapely.union_all(piece_array[right[start:end]]))
    return shapely.make_valid(geoms)


def flatten(parcels):
    """Remove double counting: where parcels are stacked on one another, the smaller one keeps the land."""
    parcels = parcels.sort_values("gross_acres").reset_index(drop=True)
    geoms = np.asarray(parcels.geometry)
    a, b = parcels.sindex.query(geoms, predicate="intersects")
    keep = a > b                                    # each pair once, with a the larger parcel
    a, b = a[keep], b[keep]
    shared = shapely.area(shapely.intersection(geoms[a], geoms[b]))
    a, b = a[shared > OVERLAP_MIN_SQFT], b[shared > OVERLAP_MIN_SQFT]
    out = geoms.copy()
    for i in np.unique(a):
        out[i] = shapely.difference(geoms[i], shapely.union_all(geoms[b[a == i]]))
    parcels["geometry"] = shapely.make_valid(out)
    return parcels


def load_parcels():
    parcels = read_parcels(["PROP_ID", "IMP_VALUE"]).to_crs(WORK_CRS)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty].copy()
    parcels["geometry"] = shapely.make_valid(np.asarray(parcels.geometry))
    parcels["improved"] = pd.to_numeric(parcels["IMP_VALUE"], errors="coerce").fillna(0) > 0
    # The source repeats some shapes (a land account and a building account, for example): keep one of each
    parcels["shape_key"] = shapely.to_wkb(shapely.normalize(np.asarray(parcels.geometry)))
    improved = parcels.groupby("shape_key")["improved"].transform("max")
    parcels = parcels.assign(improved=improved).drop_duplicates("shape_key").drop(columns=["shape_key", "IMP_VALUE"])
    parcels["gross_acres"] = acres(parcels.geometry)
    stacked_total = parcels["gross_acres"].sum()
    parcels = flatten(parcels)
    parcels["acres"] = acres(parcels.geometry)
    parcels = parcels[parcels["acres"] > 0].reset_index(drop=True)
    return parcels, stacked_total


def assign_rings(parcels):
    rings = gpd.read_file(DATA / "drive_times.geojson").to_crs(WORK_CRS).set_index("minutes")
    points = parcels.geometry.representative_point()
    ring = pd.Series(BEYOND, index=parcels.index)
    for minutes, label in reversed(RINGS):
        ring[points.within(rings.geometry[minutes])] = label
    return ring


def main():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    parcels, stacked_total = load_parcels()
    parcels["ring"] = assign_rings(parcels)
    county = gpd.read_file(DATA / "grimes_county.geojson").to_crs(WORK_CRS)
    county_acres = float(acres(county.geometry).sum())
    print(f"Parcels: {len(parcels):,} shapes, {parcels['acres'].sum():,.0f} acres after removing "
          f"{stacked_total - parcels['acres'].sum():,.0f} acres of stacked parcels. County area: {county_acres:,.0f} acres.")

    # Constraints
    zone = gpd.read_file(DATA / "reinvestment_zone.geojson").to_crs(WORK_CRS)
    holdings = gpd.read_file(DATA / "project_holdings.geojson").to_crs(WORK_CRS)
    flood = gpd.read_file(DATA / "floodplains.geojson").to_crs(WORK_CRS)
    flood = flood[flood["Flood zone"].isin(FLOOD_ZONES_REMOVED)]
    pipes = gpd.read_file(DATA / "pipelines.geojson").to_crs(WORK_CRS)
    pipes = pipes[pipes["Status"].isin(PIPELINE_STATUSES)]
    power = gpd.read_file(DATA / "power_lines.geojson").to_crs(WORK_CRS)

    site_pieces = in_pieces(pd.concat([zone.geometry, holdings.geometry]).make_valid())
    flood_pieces = in_pieces(flood.geometry.make_valid())
    after_site = subtract(parcels.geometry, site_pieces)
    after_flood = subtract(after_site, flood_pieces)
    parcels["acres_site"] = parcels["acres"] - acres(after_site)
    parcels["acres_flood"] = acres(after_site) - acres(after_flood)

    # Road frontage, measured on the parcel as drawn (before anything is removed)
    roads = fetch_roads(county, ROAD_PREFIXES).to_crs(WORK_CRS)
    road_line = shapely.union_all(np.asarray(roads.geometry))
    distance = shapely.distance(np.asarray(parcels.geometry), road_line)
    parcels["road_ft"] = distance
    parcels["frontage"] = np.where(distance <= FRONTAGE_FT, "Touches a state highway or FM road", "No state road frontage")

    results = {}
    for name, s in SCENARIOS.items():
        pipe_pieces = in_pieces(pipes.geometry.buffer(s["pipeline_ft"]))
        power_pieces = in_pieces(power.geometry.buffer(s["power_ft"]))
        after_pipe = subtract(after_flood, pipe_pieces)
        after_power = subtract(after_pipe, power_pieces)
        p = parcels.copy()
        p["acres_pipeline"] = acres(after_flood) - acres(after_pipe)
        p["acres_power"] = acres(after_pipe) - acres(after_power)
        p["buildable_acres"] = acres(after_power)
        p["geometry"] = after_power
        results[name] = p

    mid = results["middle"]
    kept = mid[mid["buildable_acres"] >= MIN_REMAINDER_ACRES].copy()
    slivers = mid.loc[mid["buildable_acres"] < MIN_REMAINDER_ACRES, "buildable_acres"].sum()
    kept["size_class"] = pd.cut(kept["buildable_acres"], [lo for lo, _, _ in SIZE_CLASSES] + [1e9], labels=SIZE_ORDER, right=False)

    # ---- Tables ----
    removed = (mid.groupby("ring")[["acres", "acres_site", "acres_flood", "acres_pipeline", "acres_power", "buildable_acres"]]
               .sum().reindex(RING_ORDER))
    removed.loc["Grimes County total"] = removed.sum()
    print("\nAcres by drive time ring, middle scenario (all parcel land, what each constraint removes, what is left):")
    print(removed.round(0).astype(int).to_string())
    print("\nShare of each ring's parcel land removed:")
    print((removed[["acres_site", "acres_flood", "acres_pipeline", "acres_power"]].div(removed["acres"], axis=0) * 100).round(1).to_string())

    by_size = kept.pivot_table(index="ring", columns="size_class", values="buildable_acres", aggfunc=["sum", "count"], observed=False).reindex(RING_ORDER)
    print("\nBuildable acres by ring and size class:")
    print(by_size["sum"].round(0).fillna(0).astype(int).to_string())
    print("\nParcels by ring and size class:")
    print(by_size["count"].fillna(0).astype(int).to_string())

    by_road = kept.pivot_table(index="ring", columns="frontage", values="buildable_acres", aggfunc=["sum", "count"]).reindex(RING_ORDER)
    print("\nBuildable acres and parcels by state road frontage:")
    print(by_road.round(0).fillna(0).astype(int).to_string())
    road_check = {ft: float(kept.loc[kept["road_ft"] <= ft, "buildable_acres"].sum()) for ft in (FRONTAGE_FT_RANGE[0], FRONTAGE_FT, FRONTAGE_FT_RANGE[1])}
    print("Buildable acres with frontage, if the distance test is", {k: round(v) for k, v in road_check.items()})

    by_use = kept.pivot_table(index="ring", columns="improved", values="buildable_acres", aggfunc=["sum", "count"]).reindex(RING_ORDER)
    print("\nBuildable acres and parcels, by whether the tax roll shows a building or other improvement (True) or not (False):")
    print(by_use.round(0).fillna(0).astype(int).to_string())

    scenario_totals = {}
    print("\nScenarios (buildable acres by ring):")
    for name, p in results.items():
        ok = p[p["buildable_acres"] >= MIN_REMAINDER_ACRES]
        scenario_totals[name] = ok.groupby("ring")["buildable_acres"].sum().reindex(RING_ORDER)
        print(f"  {name:7}", {k: round(v) for k, v in scenario_totals[name].items()}, "total", round(scenario_totals[name].sum()),
              "| pipeline", round(p["acres_pipeline"].sum()), "power", round(p["acres_power"].sum()))
    print(f"Slivers under {MIN_REMAINDER_ACRES} acre dropped: {slivers:,.0f} acres.")
    print("\nBiggest parcels left in the 15-minute ring (acres only):",
          kept[kept["ring"] == RINGS[0][1]]["buildable_acres"].nlargest(8).round(0).astype(int).tolist())
    print("Median buildable parcel, acres:", kept.groupby("ring")["buildable_acres"].median().reindex(RING_ORDER).round(1).to_dict())

    # ---- Public output: totals only ----
    public = (kept.groupby(["ring", "size_class", "frontage", "improved"], observed=True)
              .agg(parcels=("buildable_acres", "size"), buildable_acres=("buildable_acres", "sum")).reset_index())
    public["improved"] = public["improved"].map({True: "Building or improvement on the tax roll", False: "No improvement on the tax roll"})
    public["buildable_acres"] = public["buildable_acres"].round(0).astype(int)
    public = public.rename(columns={"ring": "drive_time_ring", "frontage": "state_road_frontage", "improved": "tax_roll_improvement"})
    public["_r"] = public["drive_time_ring"].map(RING_ORDER.index)
    public["_s"] = public["size_class"].astype(str).map(SIZE_ORDER.index)
    public = public.sort_values(["_r", "_s", "state_road_frontage", "tax_roll_improvement"]).drop(columns=["_r", "_s"])
    public.to_csv(OUTPUTS / "part1_buildable_land.csv", index=False)
    removed_out = removed.round(0).astype(int).rename(columns={
        "acres": "parcel_acres", "acres_site": "removed_zone_and_project_holdings", "acres_flood": "removed_floodway_and_100_year_floodplain",
        "acres_pipeline": "removed_pipeline_buffer", "acres_power": "removed_transmission_line_buffer"})
    removed_out.index.name = "drive_time_ring"
    removed_out.to_csv(OUTPUTS / "part1_constraints_by_ring.csv")

    # ---- Private output: parcel level ----
    private = gpd.GeoDataFrame(kept[["PROP_ID", "ring", "size_class", "frontage", "road_ft", "improved", "acres", "acres_site", "acres_flood",
                                     "acres_pipeline", "acres_power", "buildable_acres"]].astype({"size_class": str}),
                               geometry=np.asarray(kept.geometry), crs=WORK_CRS)
    private.to_file(PRIVATE / "part1_parcels.gpkg", driver="GPKG")
    roads.to_file(CACHE / "analysis_state_roads.gpkg", driver="GPKG")

    # ---- Key numbers ----
    parcel_source = "TxGIO StratMap Land Parcels, Grimes County (appraisal district records)"
    parcel_date = "2026-03-01"
    t = removed.loc["Grimes County total"]
    r15, r30 = RINGS[0][1], RINGS[1][1]
    numbers = [
        ("Parcel land in Grimes County, stacked parcels counted once", t["acres"], "acres", parcel_source, parcel_date, "high"),
        ("Removed: reinvestment zone and project-linked holdings", t["acres_site"], "acres",
         "County reinvestment zone order and Grimes Central Appraisal District parcel records", layer_date("holdings"), "high"),
        ("Removed: floodway and 100-year floodplain", t["acres_flood"], "acres", "FEMA National Flood Hazard Layer", layer_date("flood"), "medium"),
        ("Removed: 50 ft each side of in-service pipelines", t["acres_pipeline"], "acres", "Railroad Commission of Texas pipeline map", layer_date("pipelines"), "medium"),
        ("Removed: 100 ft each side of transmission lines", t["acres_power"], "acres", "HIFLD electric transmission lines (frozen federal dataset)", layer_date("power"), "medium"),
        ("Buildable land, Grimes County, middle scenario", scenario_totals["middle"].sum(), "acres", parcel_source + " less the constraints above", parcel_date, "medium"),
        ("Buildable land, low to high buffer scenarios", f"{scenario_totals['high'].sum():.0f} to {scenario_totals['low'].sum():.0f}", "acres", "Same, with wide and narrow buffers", parcel_date, "medium"),
        ("Buildable land within 15 minutes of the site", scenario_totals["middle"][r15], "acres", "Same, with Valhalla drive times on OpenStreetMap roads", layer_date("drive"), "medium"),
        ("Buildable land 15 to 30 minutes from the site", scenario_totals["middle"][r30], "acres", "Same", layer_date("drive"), "medium"),
        ("Share of parcel land within 15 minutes removed by floodplain", 100 * removed.loc[r15, "acres_flood"] / removed.loc[r15, "acres"], "percent", "FEMA National Flood Hazard Layer", layer_date("flood"), "medium"),
        ("Buildable land in parcels of 100 acres or more, within 30 minutes",
         kept[kept["ring"].isin([r15, r30]) & (kept["size_class"] == SIZE_ORDER[3])]["buildable_acres"].sum(), "acres", "Same", parcel_date, "medium"),
        ("Parcels with 100 or more buildable acres, within 30 minutes",
         int((kept["ring"].isin([r15, r30]) & (kept["size_class"] == SIZE_ORDER[3])).sum()), "parcels", "Same", parcel_date, "medium"),
        ("Buildable land touching a state highway or FM road, Grimes County", road_check[FRONTAGE_FT], "acres",
         "TxDOT Roadways centerlines; parcel within 150 ft of the centerline", "2026-10-01", "medium"),
        ("Buildable land with no building or improvement on the tax roll, Grimes County", kept.loc[~kept["improved"], "buildable_acres"].sum(), "acres",
         parcel_source + " (improvement value field)", parcel_date, "low"),
    ]
    save_key_numbers("Part 1: Buildable land", [
        {"metric": m, "value": (round(float(v)) if u in ("acres", "parcels") else round(float(v), 1)) if not isinstance(v, str) else v,
         "unit": u, "source": s, "date": d, "confidence": c} for m, v, u, s, d, c in numbers])
    (PRIVATE / "part1_settings_used.json").write_text(json.dumps(
        {"scenarios": SCENARIOS, "pipeline_statuses": PIPELINE_STATUSES, "flood_zones_removed": FLOOD_ZONES_REMOVED,
         "min_remainder_acres": MIN_REMAINDER_ACRES, "frontage_ft": FRONTAGE_FT, "road_prefixes": ROAD_PREFIXES}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
