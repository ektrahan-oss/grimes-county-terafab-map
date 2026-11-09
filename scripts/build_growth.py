"""Growth since 1985: transparent PNGs of developed land for the map's year slider.

Reads USGS Annual NLCD Collection 1.2 land cover, one lc_YYYY.npz per year (30 m, EPSG:5070), as written by
.cache/nlcd/extract.py. Developed means classes 21 to 24. Gray is land already developed in 1985, orange is
land developed since. Everything outside the eight counties is left clear.

Each year gets one overview image at 120 m for zoomed-out views, and 30 m pieces the map loads only when
zoomed in, so a phone never holds the whole area at full detail.

Usage: python scripts/build_growth.py [folder ...]   (default .cache/nlcd/seven and .cache/nlcd/harris)
Several folders are joined into one grid; they are all cut from the same national 30 m grid, so pixels line up.
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SRCS = [Path(a) for a in sys.argv[1:]] or [ROOT / ".cache" / "nlcd" / "seven", ROOT / ".cache" / "nlcd" / "harris"]
COUNTIES = ROOT / ".cache" / "nlcd" / "growth_counties.geojson"
OUT = ROOT / "data" / "growth"
YEARS = [1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025]
DEVELOPED = [21, 22, 23, 24]
GROUND_M = 30                  # target pixel size on the ground, the source's own
PIECE = 1024                   # pixels per side of each full-detail piece
SHRINK = 4                     # overview is 4 x 4 pixels to one, about 120 m
OVERVIEW_SHARE = 5             # an overview pixel is developed if at least 5 of its 16 are
PALETTE = [0, 0, 0, 110, 110, 110, 240, 123, 30]     # clear, gray (by 1985), orange (since)
ACRE_M2 = 4046.8564224

to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
to_albers = Transformer.from_crs(3857, 5070, always_xy=True)
to_lnglat = Transformer.from_crs(3857, 4326, always_xy=True)

years = [y for y in YEARS if all((s / f"lc_{y}.npz").exists() for s in SRCS)]
if 1985 not in years:
    sys.exit(f"No lc_1985.npz in every one of {SRCS}; 1985 is the baseline")
missing = [y for y in YEARS if y not in years]
if missing:
    print("Skipped, not in every folder:", missing)


def mosaic(y):
    """One year's land cover from every folder, joined onto a single grid. 0 means no data."""
    parts = [np.load(s / f"lc_{y}.npz") for s in SRCS]
    px = float(parts[0]["gt"][1])
    for p in parts:
        assert p["gt"][1] == px and all(abs((v - parts[0]["gt"][i]) / px - round((v - parts[0]["gt"][i]) / px)) < 1e-6
                                        for i, v in ((0, p["gt"][0]), (2, p["gt"][2]))), "folders are not on the same grid"
    x0 = min(float(p["gt"][0]) for p in parts)
    y0 = max(float(p["gt"][2]) for p in parts)
    x1 = max(float(p["gt"][0]) + p["lc"].shape[1] * px for p in parts)
    y1 = min(float(p["gt"][2]) - p["lc"].shape[0] * px for p in parts)
    out = np.zeros((round((y0 - y1) / px), round((x1 - x0) / px)), np.uint8)
    for p in parts:
        r, c = round((y0 - float(p["gt"][2])) / px), round((float(p["gt"][0]) - x0) / px)
        lc = p["lc"]
        view = out[r:r + lc.shape[0], c:c + lc.shape[1]]
        np.copyto(view, lc, where=lc > 0)
    return out, (x0, px, y0)


src = {}
for y in years:
    lc, g = mosaic(y)
    src[y] = {"lc": lc, "gt": g}
gt = src[1985]["gt"]
shape = src[1985]["lc"].shape
for y in years:
    assert np.allclose(src[y]["gt"], gt) and src[y]["lc"].shape == shape, f"{y} is on a different grid"
x0, px, y0 = gt

# Target grid in web Mercator, covering the counties and no more than the source holds
counties = json.loads(COUNTIES.read_text())
rings = []
for f in counties["features"]:
    g = f["geometry"]
    for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
        rings.extend(np.array(to_merc.transform(*np.array(r).T)).T for r in poly)
outer = np.vstack(rings)
src_edge = np.array([(x0 + c * px, y0 - r * px) for r in np.linspace(0, shape[0], 50) for c in (0, shape[1])] +
                    [(x0 + c * px, y0 - r * px) for c in np.linspace(0, shape[1], 50) for r in (0, shape[0])])
src_merc = np.array(Transformer.from_crs(5070, 3857, always_xy=True).transform(*src_edge.T)).T
west, south = np.maximum(outer.min(0), src_merc.min(0))
east, north = np.minimum(outer.max(0), src_merc.max(0))
mid_lat = to_lnglat.transform((west + east) / 2, (south + north) / 2)[1]
step = GROUND_M / np.cos(np.radians(mid_lat))            # Mercator meters per target pixel
W, H = int(np.ceil((east - west) / step)), int(np.ceil((north - south) / step))
W, H = -(-W // SHRINK) * SHRINK, -(-H // SHRINK) * SHRINK
print(f"target {W} x {H} pixels at {GROUND_M} m, from", ", ".join(str(s) for s in SRCS))

# Which source pixel each target pixel takes (nearest), worked out once for all years
index = np.full(H * W, -1, np.int32)
cols = west + (np.arange(W) + 0.5) * step
for r in range(H):
    ax, ay = to_albers.transform(cols, np.full(W, north - (r + 0.5) * step))
    sc = np.floor((ax - x0) / px).astype(np.int64)
    sr = np.floor((y0 - ay) / px).astype(np.int64)
    ok = (sc >= 0) & (sc < shape[1]) & (sr >= 0) & (sr < shape[0])
    index[r * W:(r + 1) * W] = np.where(ok, sr * shape[1] + sc, -1)

mask = Image.new("1", (W, H), 0)
draw = ImageDraw.Draw(mask)
for f in counties["features"]:
    g = f["geometry"]
    for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
        for i, ring in enumerate(poly):
            mx, my = to_merc.transform(*np.array(ring).T)
            draw.polygon(list(zip((mx - west) / step, (north - my) / step)), fill=0 if i else 1)
inside = (np.asarray(mask, bool).ravel()) & (index >= 0)
gaps = int((np.asarray(mask, bool).ravel() & ((index < 0) | (src[1985]["lc"].ravel()[np.clip(index, 0, None)] == 0))).sum())
print(f"pixels inside the counties with no land cover: {gaps:,}" + ("" if gaps < 1000 else "  <- extract a wider box"))

# Ground area of one pixel on each row, for the acre counts
row_lat = np.array(to_lnglat.transform(np.full(H, west), north - (np.arange(H) + 0.5) * step))[1]
row_acres = (step * np.cos(np.radians(row_lat))) ** 2 / ACRE_M2


def developed(y):
    lc = src[y]["lc"].ravel()
    return np.isin(lc[np.clip(index, 0, None)], DEVELOPED) & inside


def save(arr, path):
    im = Image.fromarray(arr, "P")
    im.putpalette(PALETTE)
    im.save(path, optimize=True, bits=2, transparency=0)


def lnglat_bounds(c0, r0, c1, r1, size):
    (lng0, lng1), (lat0, lat1) = to_lnglat.transform([west + c0 * size, west + c1 * size], [north - r1 * size, north - r0 * size])
    return [[round(lat0, 6), round(lng0, 6)], [round(lat1, 6), round(lng1, 6)]]


# Clear old pictures file by file: OneDrive can hold a lock on the folders themselves
for old in OUT.rglob("*") if OUT.exists() else []:
    if old.is_file():
        old.unlink()
OUT.mkdir(parents=True, exist_ok=True)
base = developed(1985)
pieces, acres, sizes = {}, {}, {}
for y in years:
    dev = developed(y)
    img = np.zeros(H * W, np.uint8)
    img[dev & base] = 1
    img[dev & ~base] = 2
    img = img.reshape(H, W)
    by1985 = float((img == 1).sum(1) @ row_acres)
    since = float((img == 2).sum(1) @ row_acres)
    acres[y] = {"by1985": round(by1985), "since": round(since)}
    folder = OUT / str(y)
    folder.mkdir(exist_ok=True)
    # Overview: a block shows if enough of it is developed, orange if more of that is new than old
    blocks = img.reshape(H // SHRINK, SHRINK, W // SHRINK, SHRINK)
    old, new = (blocks == 1).sum((1, 3)), (blocks == 2).sum((1, 3))
    ov = np.where(old + new >= OVERVIEW_SHARE, np.where(new > old, 2, 1), 0).astype(np.uint8)
    save(ov, folder / "overview.png")
    names = []
    for r in range(0, H, PIECE):
        for c in range(0, W, PIECE):
            part = img[r:r + PIECE, c:c + PIECE]
            if part.any():
                name = f"{r // PIECE}_{c // PIECE}"
                save(part, folder / f"{name}.png")
                names.append(name)
                pieces.setdefault(name, lnglat_bounds(c, r, c + part.shape[1], r + part.shape[0], step))
    sizes[y] = sum(p.stat().st_size for p in folder.iterdir())
    print(y, f"developed by 1985: {acres[y]['by1985']:,} acres, since: {acres[y]['since']:,} acres,",
          f"{len(names)} pieces, {sizes[y] / 1e6:.2f} MB")
    acres[y]["pieces"] = names

index_json = {
    "source": "USGS Annual National Land Cover Database, Collection 1.2",
    "developed_classes": DEVELOPED,
    "built": date.today().isoformat(),
    "years": years,
    "overview": lnglat_bounds(0, 0, W, H, step),
    "overview_meters": GROUND_M * SHRINK,
    "piece_meters": GROUND_M,
    "bounds": pieces,
    "by_year": {str(y): acres[y] for y in years},
}
(OUT / "index.json").write_text(json.dumps(index_json, separators=(",", ":")))
total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
print(f"total {total / 1e6:.2f} MB in {OUT}")
