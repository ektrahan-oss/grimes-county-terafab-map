"""Part 2: Infrastructure access.

For the buildable land from Part 1, how much lies within 1, 3 and 5 miles of a natural gas
transmission pipeline, a 138 kV or 345 kV power line, and a state highway. Also lists the gas
transmission lines nearest the site, since the planned on-site power plants will need gas.

Being near a line is not the same as being able to connect to it. A tap on a transmission
pipeline or a high-voltage line is negotiated with the operator and can cost millions.

Needs Part 1 to have been run (it reads analysis/private/part1_parcels.gpkg).

    python analysis/part2_infrastructure_access.py
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from charts import BLUES, INK, finish, new_chart
from common import CACHE, DATA, FEET_PER_MILE, OUTPUTS, PRIVATE, RING_ORDER, WORK_CRS, layer_date, save_key_numbers

# ---------------------------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------------------------
DISTANCES_MILES = [1, 3, 5]
GAS = {"Type": "Transmission", "Commodity": "Natural gas", "Status": "In Service"}   # gathering lines are left out: they collect from wells
VOLTAGES_KV = [138, 345]
HIGHWAY_PREFIXES = ["SH"]                # "state highway" as the plan words it; FM roads are covered in Part 1's frontage test
NEAR_SITE_MILES = 5                      # gas transmission lines listed if this close to the reinvestment zone
LARGE_PIPE_INCHES = 16                   # lines this size or larger are called out as large-diameter
# A parcel is counted as within a distance if any part of its buildable land is. No scenarios here:
# the three distances are themselves the range.
# ---------------------------------------------------------------------------------------------

LABELS = {"gas": "Gas transmission pipeline", "power": "138 or 345 kV power line", "highway": "State highway"}


def main():
    parcels = gpd.read_file(PRIVATE / "part1_parcels.gpkg")
    geoms = np.asarray(parcels.geometry)

    pipes = gpd.read_file(DATA / "pipelines.geojson").to_crs(WORK_CRS)
    gas = pipes[(pipes["Type"] == GAS["Type"]) & (pipes["Commodity"] == GAS["Commodity"]) & (pipes["Status"] == GAS["Status"])]
    power = gpd.read_file(DATA / "power_lines.geojson").to_crs(WORK_CRS)
    power = power[power["Voltage (kV)"].isin(VOLTAGES_KV)]
    roads = gpd.read_file(CACHE / "analysis_state_roads.gpkg").to_crs(WORK_CRS)
    highways = roads[roads["RTE_PRFX"].isin(HIGHWAY_PREFIXES)]

    features = {"gas": gas.geometry, "power": power.geometry, "highway": highways.geometry,
                "power_345": power[power["Voltage (kV)"] == 345].geometry}
    for key, lines in features.items():
        parcels[f"{key}_mi"] = shapely.distance(geoms, shapely.union_all(np.asarray(lines))) / FEET_PER_MILE

    total = parcels["buildable_acres"].sum()
    rows = []
    for key in ("gas", "power", "highway"):
        for miles in DISTANCES_MILES:
            near = parcels[parcels[f"{key}_mi"] <= miles]
            by_ring = near.groupby("ring")["buildable_acres"].sum().reindex(RING_ORDER).fillna(0)
            rows.append({"infrastructure": LABELS[key], "within_miles": miles, "parcels": len(near),
                         "buildable_acres": round(near["buildable_acres"].sum()), "share_of_buildable_percent": round(100 * near["buildable_acres"].sum() / total, 1),
                         **{f"acres_{r.lower().replace(' ', '_')}": round(v) for r, v in by_ring.items() if r != RING_ORDER[-1]}})
    table = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(f"Buildable land in Grimes County: {total:,.0f} acres in {len(parcels):,} parcels.\n")
    print(table.to_string(index=False))

    # All three at once
    combos = []
    for miles in DISTANCES_MILES:
        near = parcels[(parcels["gas_mi"] <= miles) & (parcels["power_mi"] <= miles) & (parcels["highway_mi"] <= miles)]
        big = near[near["buildable_acres"] >= 100]
        combos.append({"within_miles": miles, "parcels": len(near), "buildable_acres": round(near["buildable_acres"].sum()),
                       "share_of_buildable_percent": round(100 * near["buildable_acres"].sum() / total, 1),
                       "parcels_100_acres_or_more": len(big), "acres_in_those": round(big["buildable_acres"].sum())})
    combos = pd.DataFrame(combos)
    print("\nWithin the distance of all three (gas transmission, 138/345 kV, state highway):")
    print(combos.to_string(index=False))
    near345 = {m: parcels.loc[parcels["power_345_mi"] <= m, "buildable_acres"].sum() for m in DISTANCES_MILES}
    print("\nBuildable acres within 1, 3, 5 miles of a 345 kV line:", {k: round(v) for k, v in near345.items()})
    far = parcels[(parcels["gas_mi"] > 5) | (parcels["power_mi"] > 5)]
    print(f"More than 5 miles from gas transmission or from a high-voltage line: {far['buildable_acres'].sum():,.0f} acres")

    # Gas lines nearest the site
    zone = gpd.read_file(DATA / "reinvestment_zone.geojson").to_crs(WORK_CRS).geometry.iloc[0]
    gas = gas.assign(zone_mi=gas.distance(zone) / FEET_PER_MILE, miles=gas.length / FEET_PER_MILE)
    nearest = (gas[gas["zone_mi"] <= NEAR_SITE_MILES].groupby(["Operator", "Diameter (inches)"])
               .agg(miles_in_county=("miles", "sum"), miles_from_zone=("zone_mi", "min")).reset_index()
               .sort_values(["miles_from_zone", "Diameter (inches)"], ascending=[True, False]))
    nearest["crosses_zone"] = np.where(nearest["miles_from_zone"] < 0.01, "yes", "no")
    nearest = nearest.round({"miles_in_county": 1, "miles_from_zone": 1})
    nearest["Operator"] = nearest["Operator"].str.title()
    print(f"\nIn-service natural gas transmission lines within {NEAR_SITE_MILES} miles of the reinvestment zone:")
    print(nearest.to_string(index=False))
    volts = power.assign(zone_mi=power.distance(zone) / FEET_PER_MILE).groupby("Voltage (kV)")["zone_mi"].min()
    print("\nNearest power line to the zone, miles:", volts.round(1).to_dict())
    hw = highways.assign(zone_mi=highways.distance(zone) / FEET_PER_MILE).groupby("MAP_LBL")["zone_mi"].min().sort_values()
    print("State highways, miles from the zone:", hw.round(1).to_dict())

    # ---- Outputs ----
    table.to_csv(OUTPUTS / "part2_infrastructure_access.csv", index=False)
    combos.to_csv(OUTPUTS / "part2_all_three.csv", index=False)
    nearest.rename(columns={"Operator": "operator", "Diameter (inches)": "diameter_inches"}).to_csv(OUTPUTS / "part2_gas_lines_near_site.csv", index=False)
    parcels.drop(columns="geometry").to_csv(PRIVATE / "part2_parcel_distances.csv", index=False)

    # ---- Chart ----
    fig, ax = new_chart("How much buildable land is near infrastructure", "Share of Grimes County's buildable acres within 1, 3 and 5 miles", height_in=5.6)
    colors, bar, gap = BLUES[3], 0.24, 0.03
    for g, key in enumerate(("gas", "power", "highway")):
        for d, miles in enumerate(DISTANCES_MILES):
            share = table[(table["infrastructure"] == LABELS[key]) & (table["within_miles"] == miles)]["share_of_buildable_percent"].iloc[0]
            y = g + (d - 1) * (bar + gap)
            ax.barh(y, share, height=bar, color=colors[d], label=f"Within {miles} mile{'s' if miles > 1 else ''}" if g == 0 else None)
            ax.text(share + 1.5, y, f"{share:.0f}%", va="center", fontsize=10, color=INK)
    ax.set_yticks(range(3), ["Gas transmission\npipeline", "138 or 345 kV\npower line", "State highway"])
    ax.invert_yaxis()
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.legend(loc="upper center", bbox_to_anchor=(0.36, -0.07), ncols=3, handlelength=1, columnspacing=1.2)
    finish(fig, "part2_infrastructure_access",
           "Sources: Railroad Commission of Texas, HIFLD, TxDOT, TxGIO parcels (March 2026). Near is not the same as able to connect.")

    # ---- Key numbers ----
    def cell(key, miles, col):
        return table[(table["infrastructure"] == LABELS[key]) & (table["within_miles"] == miles)][col].iloc[0]
    sources = {"gas": ("Railroad Commission of Texas pipeline map; in-service natural gas transmission lines in Grimes County", layer_date("pipelines")),
               "power": ("HIFLD electric transmission lines, 138 and 345 kV (frozen federal dataset)", layer_date("power")),
               "highway": ("TxDOT Roadways centerlines, state highways", "2026-10-01")}
    numbers = []
    for key in ("gas", "power", "highway"):
        for miles in DISTANCES_MILES:
            numbers.append({"metric": f"Buildable land within {miles} mile{'s' if miles > 1 else ''} of a {LABELS[key].lower()}",
                            "value": int(cell(key, miles, "buildable_acres")), "unit": "acres", "source": sources[key][0], "date": sources[key][1], "confidence": "medium"})
    numbers += [
        {"metric": "Buildable land within 1 mile of gas transmission, a 138 or 345 kV line and a state highway", "value": int(combos["buildable_acres"].iloc[0]),
         "unit": "acres", "source": "All three sources above", "date": layer_date("pipelines"), "confidence": "medium"},
        {"metric": "Parcels with 100 or more buildable acres within 1 mile of all three", "value": int(combos["parcels_100_acres_or_more"].iloc[0]),
         "unit": "parcels", "source": "All three sources above", "date": layer_date("pipelines"), "confidence": "medium"},
        {"metric": "Buildable land within 3 miles of a 345 kV line", "value": round(near345[3]), "unit": "acres", "source": sources["power"][0], "date": sources["power"][1], "confidence": "medium"},
        {"metric": "In-service gas transmission operators with a line crossing the reinvestment zone", "value": int(nearest[nearest["crosses_zone"] == "yes"]["Operator"].nunique()),
         "unit": "operators", "source": sources["gas"][0], "date": sources["gas"][1], "confidence": "medium"},
        {"metric": "Largest gas transmission line crossing the reinvestment zone", "value": float(nearest[nearest["crosses_zone"] == "yes"]["Diameter (inches)"].max()),
         "unit": "inches diameter", "source": sources["gas"][0], "date": sources["gas"][1], "confidence": "medium"},
    ]
    save_key_numbers("Part 2: Infrastructure access", numbers)


if __name__ == "__main__":
    main()
