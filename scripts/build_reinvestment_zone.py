"""Build the SpaceX Reinvestment Zone layer in data/reinvestment_zone.geojson.

The zone is defined by a list of appraisal district property IDs ("R-numbers").
This script reads that list from two county documents, matches it to parcel
polygons, and dissolves the matched parcels into one outline.

    python scripts/build_reinvestment_zone.py           # report only
    python scripts/build_reinvestment_zone.py --write   # also write the GeoJSON

Sources
  Proposed packet  County map packet rendered from the submitted R-numbers.
                   Has a text layer, so R-numbers are read straight from it.
  Final order      Order designating Reinvestment Zone No. 01-2026-001. It is a
                   scan with no text layer, and OCR misreads its R-numbers, so
                   Exhibit B was transcribed by hand into
                   data/sources/final_order_exhibit_b.csv. That transcript is
                   tied to one exact PDF (ORDER_SHA256). If the county replaces
                   the PDF, the script stops until the transcript is redone.
  Parcels          TxGIO StratMap Land Parcels for Grimes County (CC0), which
                   TxGIO compiles from Grimes Central Appraisal District.

Needs Python 3.9+ and: pip install -r scripts/requirements.txt
"""

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import pypdf
import shapely
from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"          # downloads; not committed

PACKET_URL = (
    "https://grimescountytx.govoffice.com/vertical/Sites/"
    "%7B958238D0-27E6-4F6C-919E-F1D98542C5FD%7D/uploads/"
    "SpaceX_Proposed_Reinvestment_Zone_Maps_-_Rendered_based_on_submitted_R-numbers.pdf"
)
# The order is linked from this page; the link is looked up each run in case the file is renamed.
ECON_DEV_PAGE = (
    "https://grimescountytexas.gov/index.asp"
    "?DE=41B01CCB-E7BC-4B13-AA5B-6E776B28053E&SEC=29DD23F1-21FF-472B-9DB3-CC5315345627"
)
ORDER_LINK = re.compile(r'href="\s*([^"]*Order_Designating_SpaceX_Reinvestment_Zone[^"]*\.pdf)"', re.I)

ORDER_TRANSCRIPT = DATA / "sources" / "final_order_exhibit_b.csv"
ORDER_SHA256 = "31d8c88934ea3c37464433f5a0c00639cbff25253794448746c1afa5e79942df"
ORDER_TOTAL_ACRES = 22435.09     # total printed at the foot of Exhibit B

# Listed in the order but left off the map. They stay in reinvestment_zone_parcels.csv.
NOT_DRAWN = {
    # 5.9 acres near Navasota, about 16 miles south of the rest and on none of the county's maps.
    # Possibly a typo in the order. Remove from this list if the county confirms it.
    "R19673",
}

TXGIO_API = "https://api.tnris.org/api/v1"
COUNTY = "Grimes"

APPROVED_DATE = "2026-06-03"
EXPECTED_ACRES = 22000
ACRES_TOLERANCE = 0.05           # warn if the outline is more than 5% off
SIMPLIFY_FEET = 15
MIN_HOLE_ACRES = 1               # smaller gaps are slivers between parcels, not real exclusions
CLOSE_GAPS_FEET = 60             # fills road strips up to 120 feet wide between listed parcels
WORK_CRS = 2277                  # Texas State Plane Central, US feet

# data.geographic.texas.gov rejects requests that don't look like this
USER_AGENT = "Mozilla/5.0 (compatible; grimes-county-terafab-map/1.0)"


# ---- Downloads ----

def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_pdf(url, name):
    body = fetch(url)
    if not body.startswith(b"%PDF"):
        sys.exit(f"{url}\ndid not return a PDF. Nothing written.")
    CACHE.mkdir(exist_ok=True)
    (CACHE / name).write_bytes(body)
    return body


def find_order_url():
    page = fetch(ECON_DEV_PAGE).decode("utf-8", "replace")
    m = ORDER_LINK.search(page)
    if not m:
        sys.exit("Could not find the Order Designating SpaceX Reinvestment Zone link on the "
                 "county economic development page. Nothing written.")
    return urllib.parse.urljoin(ECON_DEV_PAGE, html.unescape(m.group(1)).strip())


def fetch_parcels(refresh):
    """Download the newest StratMap Land Parcels release for the county. Returns (zip path, label)."""
    CACHE.mkdir(exist_ok=True)
    cached = sorted(CACHE.glob("stratmap*-landparcels_*.zip"))
    if cached and not refresh:
        return cached[-1], cached[-1].stem

    catalog = json.loads(fetch(f"{TXGIO_API}/collections_catalog/?name=Land%20Parcels"))
    newest = max(catalog["results"], key=lambda c: c["acquisition_date"])
    resources = json.loads(fetch(
        f"{TXGIO_API}/resources/?collection_id={newest['collection_id']}&area_type=county&limit=400"))
    hits = [r for r in resources["results"] if r["area_type_name"] == COUNTY]
    if not hits:
        sys.exit(f"TxGIO has no {COUNTY} County file in its newest Land Parcels release.")
    url = hits[0]["resource"]
    path = CACHE / url.rsplit("/", 1)[1]
    path.write_bytes(fetch(url, timeout=600))
    return path, path.stem


# ---- R-numbers ----

def norm_id(value):
    """'R11032', 'r 11032', '11032', 11032.0 -> '11032'."""
    s = re.sub(r"\.0$", "", str(value).strip().upper())
    return s.lstrip("R").strip().lstrip("0")


def natural(r_number):
    return int(norm_id(r_number))


def pdf_r_numbers(pdf_bytes_path):
    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(pdf_bytes_path).pages)
    return set(re.findall(r"R\d+", text))


def order_r_numbers(order_sha):
    """R-numbers in the final order: from its text layer if it has one, else the transcript."""
    found = pdf_r_numbers(CACHE / "final_order.pdf")
    if found:
        return found, "text layer"
    if order_sha != ORDER_SHA256:
        sys.exit(
            "The final order PDF has changed since Exhibit B was transcribed, and it has no text "
            f"layer to read.\nRe-transcribe it into {ORDER_TRANSCRIPT.relative_to(ROOT)}, then set "
            f"ORDER_SHA256 in this script to:\n  {order_sha}")
    rows = list(csv.DictReader(ORDER_TRANSCRIPT.open(encoding="utf-8")))
    total = sum(float(r["acres"]) for r in rows)
    if abs(total - ORDER_TOTAL_ACRES) > 0.01:
        sys.exit(f"Transcript acres add up to {total:,.2f}, but the order prints "
                 f"{ORDER_TOTAL_ACRES:,.2f}. Fix {ORDER_TRANSCRIPT.relative_to(ROOT)}.")
    return {r["r_number"] for r in rows}, "hand transcript of Exhibit B"


def read_previous_lists():
    path = DATA / "reinvestment_zone_parcels.csv"
    if not path.exists():
        return None
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return {col: {r["r_number"] for r in rows if r[col] == "yes"}
            for col in ("in_proposed_packet", "in_final_order")}


def write_parcel_csv(packet, order):
    with (DATA / "reinvestment_zone_parcels.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["r_number", "in_proposed_packet", "in_final_order"])
        for r in sorted(packet | order, key=natural):
            w.writerow([r, "yes" if r in packet else "no", "yes" if r in order else "no"])


# ---- Source hashes and change log ----

def list_change(label, before, after):
    added, removed = sorted(after - before, key=natural), sorted(before - after, key=natural)
    parts = []
    if added:
        parts.append("added " + ", ".join(added))
    if removed:
        parts.append("removed " + ", ".join(removed))
    return f"{label}: " + ("; ".join(parts) if parts else "file changed, R-number list unchanged")


def record_sources(sources, previous_lists, current_lists):
    """Save PDF hashes. If one differs from last run, log what changed in data/changes.json."""
    hash_path, change_path = DATA / "source_hashes.json", DATA / "changes.json"
    old = json.loads(hash_path.read_text(encoding="utf-8")) if hash_path.exists() else {}
    changes = json.loads(change_path.read_text(encoding="utf-8")) if change_path.exists() else []
    today = date.today().isoformat()

    new_entries = []
    for key, src in sources.items():
        if key in old and old[key]["sha256"] != src["sha256"]:
            col = "in_proposed_packet" if key == "proposed_packet" else "in_final_order"
            if previous_lists and current_lists.get(col) is not None:
                what = list_change(src["title"], previous_lists[col], current_lists[col])
            else:
                what = f"{src['title']}: file changed"
            new_entries.append({"date": today, "source": key, "change": what,
                                "old_sha256": old[key]["sha256"], "new_sha256": src["sha256"]})
    if new_entries:
        change_path.write_text(json.dumps(changes + new_entries, indent=2) + "\n", encoding="utf-8")
    hash_path.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    return new_entries


# ---- Geometry ----

def load_parcels(zip_path):
    """Read only the property ID and shape. Owner names and addresses are never loaded."""
    shp = next(n for n in zipfile.ZipFile(zip_path).namelist() if n.lower().endswith(".shp"))
    parcels = gpd.read_file(f"zip://{zip_path}!{shp}", columns=["Prop_ID"])
    parcels["id"] = parcels["Prop_ID"].map(norm_id)
    return parcels[["id", "geometry"]]


def polygons(geom):
    """Every polygon inside a geometry, however it is nested."""
    if geom.geom_type == "Polygon":
        return [] if geom.is_empty else [geom]
    return [p for part in getattr(geom, "geoms", []) for p in polygons(part)]


def drop_small_holes(geom):
    min_area = MIN_HOLE_ACRES * 43560
    return MultiPolygon([Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area >= min_area])
                         for p in polygons(geom)])


def build_outline(matched):
    """Dissolve parcels into one outline. Returns (web outline in EPSG:4326, acres before simplifying)."""
    parcels = matched.to_crs(WORK_CRS).geometry.union_all()
    # The parcel map leaves thin strips with no parcel where roads run between listed tracts. Left
    # alone they show as stray lines inside the zone, so strips narrower than twice CLOSE_GAPS_FEET
    # are filled in. Acreage is still measured from the parcels themselves.
    closed = parcels.buffer(CLOSE_GAPS_FEET, join_style="mitre").buffer(-CLOSE_GAPS_FEET, join_style="mitre")
    outline = drop_small_holes(shapely.unary_union([closed, parcels]))
    # Simplifying and rounding can each make neighboring pieces touch or cross, so repair after both.
    simple = shapely.make_valid(outline.simplify(SIMPLIFY_FEET, preserve_topology=True))
    wgs = gpd.GeoSeries([outline, simple], crs=WORK_CRS).to_crs(4326)
    web = shapely.make_valid(shapely.set_precision(wgs.iloc[1], 1e-6))
    measured = gpd.GeoSeries([drop_small_holes(parcels)], crs=WORK_CRS).to_crs(4326).iloc[0]
    acres = abs(Geod(ellps="WGS84").geometry_area_perimeter(measured)[0]) / 4046.8564224
    return MultiPolygon(polygons(web)), acres


def rounded(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [rounded(c) for c in coords]


def write_geojson(outline, acres, n_parcels, n_skipped, parcel_label):
    which = f"the {n_parcels} parcels"
    left_off = ""
    if n_skipped:
        which = f"{n_parcels - n_skipped} of the {n_parcels} parcels"
        left_off = "Left off: listed land far from the rest that is on none of the county's maps. "
    left_off += "Road strips between listed parcels are filled in for readability. "
    props = {
        "name": "SpaceX Reinvestment Zone No. 01-2026-001",
        "approved_date": APPROVED_DATE,
        "acres": round(acres),
        "source": f"Grimes County Commissioners Court order of June 3, 2026; parcel shapes from TxGIO {parcel_label}",
        "description": (
            f"Outline of {which} listed in the county order. The order lists "
            f"{ORDER_TOTAL_ACRES:,.2f} acres; the figure here is measured from the mapped outline. "
            + left_off + "Approximate, not a survey."),
    }
    geometry = outline.__geo_interface__
    geometry = {"type": geometry["type"], "coordinates": rounded(geometry["coordinates"])}
    feature = ('    {"type": "Feature",\n     "properties": ' + json.dumps(props, ensure_ascii=False)
               + ',\n     "geometry": ' + json.dumps(geometry, separators=(",", ":")) + "}")
    (DATA / "reinvestment_zone.geojson").write_text(
        '{\n  "type": "FeatureCollection",\n  "features": [\n' + feature + "\n  ]\n}\n", encoding="utf-8")


# ---- Main ----

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="write data/reinvestment_zone.geojson and update data/meta.json")
    ap.add_argument("--refresh-parcels", action="store_true",
                    help="download the parcel file again instead of using the cached copy")
    args = ap.parse_args()

    # 1. Source PDFs
    order_url = find_order_url()
    packet_pdf = fetch_pdf(PACKET_URL, "proposed_packet.pdf")
    order_pdf = fetch_pdf(order_url, "final_order.pdf")
    today = date.today().isoformat()
    sources = {
        "proposed_packet": {"title": "Proposed Reinvestment Zone map packet", "url": PACKET_URL,
                            "sha256": hashlib.sha256(packet_pdf).hexdigest(), "bytes": len(packet_pdf), "checked": today},
        "final_order": {"title": "Order Designating SpaceX Reinvestment Zone No. 01-2026-001", "url": order_url,
                        "sha256": hashlib.sha256(order_pdf).hexdigest(), "bytes": len(order_pdf), "checked": today},
    }

    # 2. R-numbers. The packet is read first so its changes get logged even if the order needs re-transcribing.
    previous = read_previous_lists()
    packet = pdf_r_numbers(CACHE / "proposed_packet.pdf")
    if not packet:
        sys.exit("No R-numbers found in the proposed packet PDF. Nothing written.")
    if sources["final_order"]["sha256"] != ORDER_SHA256 and not pdf_r_numbers(CACHE / "final_order.pdf"):
        record_sources(sources, previous, {"in_proposed_packet": packet, "in_final_order": None})
    order, order_method = order_r_numbers(sources["final_order"]["sha256"])

    changes = record_sources(sources, previous, {"in_proposed_packet": packet, "in_final_order": order})
    write_parcel_csv(packet, order)

    print(f"Proposed packet: {len(packet)} R-numbers")
    print(f"Final order:     {len(order)} R-numbers ({order_method})")
    print(f"  in the packet but not the order: {', '.join(sorted(packet - order, key=natural)) or 'none'}")
    print(f"  in the order but not the packet: {', '.join(sorted(order - packet, key=natural)) or 'none'}")
    for c in changes:
        print(f"SOURCE CHANGED since last run. {c['change']}")

    # 3. Parcels. The final order defines the zone.
    zip_path, parcel_label = fetch_parcels(args.refresh_parcels)
    parcels = load_parcels(zip_path)
    skipped = sorted(order & NOT_DRAWN, key=natural)
    wanted = {norm_id(r): r for r in order - NOT_DRAWN}
    matched = parcels[parcels["id"].isin(wanted)]
    unmatched = sorted((r for i, r in wanted.items() if i not in set(matched["id"])), key=natural)
    print(f"\nParcels: {parcel_label} ({len(parcels):,} parcels in the county)")
    print(f"  matched {len(wanted) - len(unmatched)} of {len(wanted)} R-numbers")
    print(f"  not matched: {', '.join(unmatched) or 'none'}")
    print(f"  in the order but deliberately not drawn: {', '.join(skipped) or 'none'}")
    if matched.empty:
        sys.exit("No parcels matched. Nothing written.")

    # 4. Outline and acreage
    outline, acres = build_outline(matched)
    points = sum(len(p.exterior.coords) + sum(len(h.coords) for h in p.interiors) for p in outline.geoms)
    print(f"\nOutline: {len(outline.geoms)} separate pieces, {points:,} points after simplifying")
    print(f"  measured from the outline: {acres:,.0f} acres")
    print(f"  listed in the order:       {ORDER_TOTAL_ACRES:,.2f} acres")
    if abs(acres - EXPECTED_ACRES) > ACRES_TOLERANCE * EXPECTED_ACRES:
        print(f"WARNING: outline is {acres:,.0f} acres, far from the roughly {EXPECTED_ACRES:,} expected.")

    # 5-7. Outputs
    if not args.write:
        print("\nReport only. Run again with --write to write data/reinvestment_zone.geojson.")
        return
    write_geojson(outline, acres, len(order), len(skipped), parcel_label)
    meta_path = DATA / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["updated"] = today
    meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    print("\nWrote data/reinvestment_zone.geojson and updated data/meta.json.")


if __name__ == "__main__":
    main()
