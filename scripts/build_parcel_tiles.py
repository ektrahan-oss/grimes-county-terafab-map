"""Cut Grimes County's parcel lines into small map tiles under data/parcels/.

Source: TxGIO StratMap Land Parcels (public domain), which TxGIO compiles from the Grimes Central
Appraisal District. Each release shows parcels as of the date the state collected them, which is
read from the file and shown on the map; tracts split or sold since then are not reflected.

The county has about 27,000 parcels, too many for one map file. Each parcel goes into the tile
its middle falls in, and the map loads only the tiles in view once zoomed in.

Only the property ID and acreage are kept. Owner names and addresses are never read from the file.

Run by hand; the parcel download is refused from GitHub's servers, so this is not part of the
automated refresh:

    python scripts/build_parcel_tiles.py
"""

import json
import math
import re
import sys
from datetime import date

import build_reinvestment_zone as zone
from common import DATA, WORK_CRS, log_change, read_parcel_file, record_layer

TILE_ZOOM = 13                   # tiles about 2.6 miles across at this latitude
SIMPLIFY_FEET = 6
OUT = DATA / "parcels"
SOURCE = "https://data.geographic.texas.gov/ (StratMap Land Parcels, Grimes County)"


def tile_of(lon, lat):
    n = 2 ** TILE_ZOOM
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def rounded(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [rounded(c) for c in coords]


def main():
    zip_path, label = zone.fetch_parcels("--refresh-parcels" in sys.argv)
    parcels = read_parcel_file(zip_path, ["Prop_ID", "DATE_ACQ"]).to_crs(WORK_CRS)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty]
    # Each parcel carries the date the state took the appraisal district's records: the number 20250101
    # in the 2025 release, a proper date in the 2026 one
    taken = re.sub(r"\D", "", str(parcels["DATE_ACQ"].mode().iloc[0]))
    as_of = f"{date(int(taken[:4]), int(taken[4:6]), 1):%B %Y}"
    parcels["id"] = "R" + parcels["Prop_ID"].map(zone.norm_id)
    parcels["acres"] = (parcels.area / 43560).round(1)
    # The source repeats some parcels; one copy of each shape is enough
    parcels = parcels.drop_duplicates(subset=["id", "acres"])
    parcels["geometry"] = parcels.geometry.simplify(SIMPLIFY_FEET)
    parcels = parcels.to_crs(4326)
    middles = parcels.geometry.representative_point()
    parcels["tile"] = [tile_of(p.x, p.y) for p in middles]

    index = OUT / "index.json"
    previous_as_of = json.loads(index.read_text(encoding="utf-8")).get("as_of") if index.exists() else None
    # Empty the folder rather than remove it: OneDrive can be holding the folder itself open
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.json"):
        stale.unlink()
    names, total = [], 0
    for (x, y), group in parcels.groupby("tile"):
        features = [{"type": "Feature", "properties": {"id": r.id, "acres": r.acres},
                     "geometry": {"type": r.geometry.geom_type, "coordinates": rounded(r.geometry.__geo_interface__["coordinates"])}}
                    for r in group.itertuples()]
        text = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
        (OUT / f"{x}_{y}.json").write_text(text + "\n", encoding="utf-8")
        names.append(f"{x}_{y}")
        total += len(text)

    old_meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("layers", {}).get("parcels", {})
    index.write_text(json.dumps(
        {"zoom": TILE_ZOOM, "as_of": as_of, "release": label, "parcels": len(parcels), "tiles": sorted(names)}) + "\n", encoding="utf-8")
    if old_meta.get("feature_count") != len(parcels) or previous_as_of != as_of:
        log_change("parcels", f"Parcel lines: built from the {as_of} parcel map, {len(parcels):,} parcels")
    record_layer("parcels", SOURCE, len(parcels))
    print(f"Wrote {len(parcels):,} parcels into {len(names)} tiles under data/parcels/ ({total / 1e6:.1f} MB in all, "
          f"largest tile {max((OUT / (n + '.json')).stat().st_size for n in names) / 1024:.0f} KB). Parcel map as of {as_of}.")


if __name__ == "__main__":
    main()
