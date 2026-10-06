"""Shared pieces for the analysis scripts: where things live, and how key numbers are saved."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"                 # downloads; not committed
OUTPUTS = ROOT / "analysis" / "outputs"  # public-safe results
CHARTS = OUTPUTS / "charts"
PRIVATE = ROOT / "analysis" / "private"  # parcel-level work; git-ignored
KEY_NUMBERS = OUTPUTS / "key_numbers.json"

WORK_CRS = 2277                         # Texas State Plane Central, US feet
SQFT_PER_ACRE = 43560
FEET_PER_MILE = 5280

RINGS = [(15, "Within 15 minutes"), (30, "15 to 30 minutes"), (60, "30 to 60 minutes")]
BEYOND = "Beyond 60 minutes"
RING_ORDER = [label for _, label in RINGS] + [BEYOND]


def layer_date(layer_id):
    """The date a map layer was last refreshed, from data/meta.json."""
    return json.loads((DATA / "meta.json").read_text(encoding="utf-8"))["layers"][layer_id]["updated"]


def save_key_numbers(section, rows):
    """Replace one section of key_numbers.json. Each row: metric, value, unit, source, date, confidence."""
    needed = {"metric", "value", "unit", "source", "date", "confidence"}
    for row in rows:
        missing = needed - set(row)
        if missing:
            raise ValueError(f"Key number {row.get('metric')} is missing {sorted(missing)}")
        if row["confidence"] not in ("high", "medium", "low"):
            raise ValueError(f"Confidence for {row['metric']} must be high, medium or low")
    kept = [r for r in json.loads(KEY_NUMBERS.read_text(encoding="utf-8"))] if KEY_NUMBERS.exists() else []
    kept = [r for r in kept if r["section"] != section]
    kept += [{"section": section, **row} for row in rows]
    KEY_NUMBERS.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {len(rows)} key numbers for {section}.")
