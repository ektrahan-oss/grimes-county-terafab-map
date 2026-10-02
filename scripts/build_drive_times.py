"""Build drive-time rings and likely routes from the Terafab site.

Writes two files:
  data/drive_times.geojson    four bands: within 15, 30, 60 and 90 minutes of the site
  data/drive_routes.geojson   the likely route to each nearby city, with minutes and miles

Source: the public Valhalla routing service run by FOSSGIS, which routes over OpenStreetMap
roads. Times assume normal speeds with no live traffic, so treat them as approximate.

The service only draws areas up to 60 minutes. The 90-minute area is estimated by drawing
30-minute areas from points spaced along the edge of the 60-minute area and joining them up.

Everything is measured from where the site fronts State Highway 30. The site marker itself
sits inside the old plant property, away from any public road.

This is a one-time layer. It is not part of the regular refresh; run it by hand only when the
rings or routes need redoing (it makes about 80 requests, spaced out):

    python scripts/build_drive_times.py
"""

import json
import sys
import time

import geopandas as gpd
import shapely
from shapely.geometry import LineString, MultiPolygon, shape

from common import DATA, WORK_CRS, fetch_json, publish_geojson

VALHALLA = "https://valhalla1.openstreetmap.de"
PAUSE_SECONDS = 1.5              # the public server asks for light use
ORIGIN = (-96.0352, 30.5978)     # State Highway 30 at the Terafab site frontage
RINGS = [15, 30, 60, 90]         # minutes
SERVICE_LIMIT = 60               # the longest area the service will draw
EDGE_SPACING_MILES = 10          # spacing of the points used to estimate areas beyond the limit
EDGE_SMOOTHING_MILES = 4
SIMPLIFY_FEET = 400

RINGS_OUT = DATA / "drive_times.geojson"
ROUTES_OUT = DATA / "drive_routes.geojson"

# Town points come from data/places.geojson; these two are not on the map as towns.
EXTRA_DESTINATIONS = {
    "Huntsville": (-95.5508, 30.7235),
    "Downtown Houston": (-95.3698, 29.7604),
}
DESTINATIONS = ["Anderson", "Navasota", "Bryan", "College Station", "Huntsville", "Downtown Houston"]

_calls = 0


def valhalla(action, request):
    global _calls
    if _calls:
        time.sleep(PAUSE_SECONDS)
    _calls += 1
    # compact JSON: the server does not accept "+" for the spaces json.dumps would otherwise add
    return fetch_json(f"{VALHALLA}/{action}", {"json": json.dumps(request, separators=(",", ":"))}, timeout=180)


def reachable(lon, lat, minutes):
    """Area reachable from a point within each of the given times, as {minutes: shape} in EPSG:4326."""
    result = valhalla("isochrone", {"locations": [{"lat": lat, "lon": lon}], "costing": "auto", "polygons": True,
                                    "contours": [{"time": m} for m in minutes], "denoise": 0.6, "generalize": 150})
    return {round(f["properties"]["contour"]): shapely.make_valid(shape(f["geometry"])) for f in result["features"]}


def decode(polyline, precision=6):
    """Valhalla's encoded route shape -> list of (lon, lat)."""
    coords, index, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    while index < len(polyline):
        for is_lon in (False, True):
            shift = value = 0
            while True:
                byte = ord(polyline[index]) - 63
                index += 1
                value |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(value >> 1) if value & 1 else value >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        coords.append((lon / factor, lat / factor))
    return coords


def point_named(path, name):
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        if f["properties"].get("name") == name:
            return tuple(f["geometry"]["coordinates"][:2])
    sys.exit(f"No point named {name} in {path.name}. Nothing written.")


def web_shape(band):
    """A band in map coordinates, rounded the way it will be stored, and repaired if rounding broke it."""
    lonlat = gpd.GeoSeries([band], crs=WORK_CRS).to_crs(4326).iloc[0]
    fixed = shapely.make_valid(shapely.set_precision(shapely.make_valid(lonlat), 1e-6))
    polygons = [g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type == "Polygon" and not g.is_empty]
    polygons += [p for g in getattr(fixed, "geoms", []) if g.geom_type == "MultiPolygon" for p in g.geoms]
    return MultiPolygon(polygons)


def build_rings():
    direct = [m for m in RINGS if m <= SERVICE_LIMIT]
    areas = reachable(*ORIGIN, direct)
    for minutes in [m for m in RINGS if m > SERVICE_LIMIT]:
        extra = minutes - SERVICE_LIMIT
        # Smooth the edge first: followed exactly, its zigzags would mean hundreds of requests
        edge = gpd.GeoSeries([areas[SERVICE_LIMIT]], crs=4326).to_crs(WORK_CRS).iloc[0]
        edge = edge.simplify(EDGE_SMOOTHING_MILES * 5280).buffer(0).boundary
        spacing = EDGE_SPACING_MILES * 5280
        stops = [line.interpolate(i * spacing) for line in getattr(edge, "geoms", [edge])
                 for i in range(int(line.length // spacing) + 1)]
        stops = gpd.GeoSeries(stops, crs=WORK_CRS).to_crs(4326)
        print(f"  Estimating the {minutes}-minute area from {len(stops)} points on the edge of the {SERVICE_LIMIT}-minute area...", flush=True)
        pieces, skipped = [areas[SERVICE_LIMIT]], 0
        for p in stops:
            try:
                pieces.append(reachable(p.x, p.y, [extra])[extra])
            except Exception:                   # a point with no road nearby
                skipped += 1
        areas[minutes] = shapely.unary_union(pieces)
        if skipped:
            print(f"  ({skipped} edge points had no road nearby and were skipped)")

    rows, inner, previous = [], None, 0
    for minutes in RINGS:
        area = gpd.GeoSeries([areas[minutes]], crs=4326).to_crs(WORK_CRS).iloc[0].buffer(0)
        area = shapely.unary_union([area, inner]) if inner is not None else area      # each area contains the one before
        band = area.difference(inner) if inner is not None else area
        label = f"{minutes} minutes" if minutes < 60 else ("1 hour" if minutes == 60 else f"{minutes / 60:g} hours")
        rows.append({
            "name": f"Within {label} of the site",
            "description": "Approximate drive time from the site's frontage on State Highway 30, without traffic."
                           + (" Estimated by extending the 1-hour area." if minutes > SERVICE_LIMIT else ""),
            "Drive time": f"{previous} to {minutes} minutes" if previous else f"{minutes} minutes or less",
            "minutes": minutes,
            "geometry": web_shape(band.simplify(SIMPLIFY_FEET)),
        })
        print(f"  {label:>10}: reaches {area.area / 5280 ** 2:,.0f} square miles")
        inner, previous = area, minutes
    return gpd.GeoDataFrame(rows, crs=4326)


def build_routes():
    rows = []
    for name in DESTINATIONS:
        end = EXTRA_DESTINATIONS.get(name) or point_named(DATA / "places.geojson", name)
        trip = valhalla("route", {"locations": [{"lat": ORIGIN[1], "lon": ORIGIN[0]}, {"lat": end[1], "lon": end[0]}],
                                  "costing": "auto", "units": "miles"})["trip"]
        minutes, miles = round(trip["summary"]["time"] / 60), round(trip["summary"]["length"], 1)
        rows.append({
            "name": f"Likely route to {name}",
            "description": "Approximate. Estimated without traffic, from the site's frontage on State Highway 30.",
            "Destination": name,
            "Drive time (minutes)": minutes,
            "Miles": miles,
            "geometry": LineString([c for leg in trip["legs"] for c in decode(leg["shape"])]),
        })
        print(f"  {minutes:>3} minutes, {miles:>5.1f} miles  {name}")
    routes = gpd.GeoDataFrame(rows, crs=4326).to_crs(WORK_CRS)
    routes["geometry"] = routes.geometry.simplify(30)
    return routes


def main():
    rings = build_rings()
    size = publish_geojson("drive", "Drive times", RINGS_OUT, rings, f"{VALHALLA}/isochrone", ("ring", "rings"), key="minutes")
    print(f"Wrote {len(rings)} rings to {RINGS_OUT.name} ({size / 1024:.0f} KB).")
    routes = build_routes()
    size = publish_geojson("routes", "Likely routes", ROUTES_OUT, routes, f"{VALHALLA}/route", ("route", "routes"), key="Destination")
    print(f"Wrote {len(routes)} routes to {ROUTES_OUT.name} ({size / 1024:.0f} KB). {_calls} requests made.")


if __name__ == "__main__":
    main()
