"""Build driving routes from the Terafab site into data/drive_times.geojson.

Source: the public OSRM demo routing service, which routes over OpenStreetMap
roads. Times are free-flow estimates with no traffic, measured from where each
route joins a public road near the site marker, so treat them as approximate.

This is a one-time layer. It is not part of the regular refresh; run it by hand
only when the routes need redoing:

    python scripts/build_drive_times.py
"""

import json
import sys
import time

import geopandas as gpd
from shapely.geometry import LineString

from common import DATA, WORK_CRS, fetch_json, publish_geojson

OSRM = "https://router.project-osrm.org/route/v1/driving"
PAUSE_SECONDS = 2                # the demo server asks for light use
SIMPLIFY_FEET = 30
OUT = DATA / "drive_times.geojson"

# Town points come from data/places.geojson; these two are not on the map as towns.
EXTRA_DESTINATIONS = {
    "Huntsville": (-95.5508, 30.7235),
    "Downtown Houston": (-95.3698, 29.7604),
}
DESTINATIONS = ["Anderson", "Navasota", "Bryan", "College Station", "Huntsville", "Downtown Houston"]


def point_named(path, name=None):
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        if name is None or f["properties"].get("name") == name:
            return tuple(f["geometry"]["coordinates"][:2])
    sys.exit(f"No point named {name} in {path.name}. Nothing written.")


def main():
    start = point_named(DATA / "site.geojson")
    rows = []
    for i, name in enumerate(DESTINATIONS):
        end = EXTRA_DESTINATIONS.get(name) or point_named(DATA / "places.geojson", name)
        if i:
            time.sleep(PAUSE_SECONDS)
        result = fetch_json(f"{OSRM}/{start[0]},{start[1]};{end[0]},{end[1]}",
                            {"overview": "false", "steps": "true", "geometries": "geojson"})
        if result.get("code") != "Ok":
            sys.exit(f"OSRM could not route to {name}: {result.get('code')}. Nothing written.")
        steps = result["routes"][0]["legs"][0]["steps"]
        # The site marker sits inside the old plant property, where OSRM starts on unnamed
        # service tracks at walking pace. Measure from where the route joins a public road.
        public = next(i for i, step in enumerate(steps) if step["name"] or step.get("ref"))
        on_site, steps = steps[:public], steps[public:]
        minutes = round(sum(step["duration"] for step in steps) / 60)
        miles = round(sum(step["distance"] for step in steps) / 1609.344, 1)
        coords = [c for step in steps for c in step["geometry"]["coordinates"]]
        rows.append({
            "name": f"Drive to {name}",
            "description": "Approximate. Estimated without traffic, from the nearest public road to the site.",
            "Destination": name,
            "Drive time (minutes)": minutes,
            "Miles": miles,
            "geometry": LineString(coords),
        })
        skipped = sum(step["distance"] for step in on_site) / 1609.344, sum(step["duration"] for step in on_site) / 60
        print(f"  {minutes:>3} minutes, {miles:>5.1f} miles  {name}  "
              f"(from {steps[0].get('ref') or steps[0]['name']}; left out {skipped[0]:.1f} miles / {skipped[1]:.0f} min of on-site tracks)")

    routes = gpd.GeoDataFrame(rows, crs=4326).to_crs(WORK_CRS)
    routes["geometry"] = routes.geometry.simplify(SIMPLIFY_FEET)
    size = publish_geojson("drive", "Drive times", OUT, routes, OSRM, ("route", "routes"), key="Destination")
    print(f"Wrote {len(routes)} routes to {OUT.name} ({size / 1024:.0f} KB).")


if __name__ == "__main__":
    main()
