"""Fetch the seven county outlines used by the growth layer and print the box to extract land cover for."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".cache" / "nlcd" / "growth_counties.geojson"
COUNTIES = ["Grimes", "Brazos", "Montgomery", "Waller", "Washington", "Walker", "Madison"]
URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"

q = {"where": "STATE='48' AND BASENAME IN (" + ",".join(f"'{c}'" for c in COUNTIES) + ")",
     "outFields": "BASENAME,GEOID", "outSR": 4326, "f": "geojson"}
with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(q), timeout=180) as r:
    data = json.load(r)
names = sorted(f["properties"]["BASENAME"] for f in data["features"])
assert names == sorted(COUNTIES), names
OUT.write_text(json.dumps(data))
xs, ys = [], []
for f in data["features"]:
    g = f["geometry"]
    for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
        for x, y in poly[0]:
            xs.append(x)
            ys.append(y)
print(f"BBOX={min(xs) - 0.01:.3f},{min(ys) - 0.01:.3f},{max(xs) + 0.01:.3f},{max(ys) + 0.01:.3f}")
