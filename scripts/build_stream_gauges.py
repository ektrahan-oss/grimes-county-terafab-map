"""Build the stream gauges layer: Navasota River flow and the level of Gibbons Creek Reservoir.

Sources:
  U.S. Geological Survey Water Data APIs (api.waterdata.usgs.gov), the replacement for the older
  waterservices.usgs.gov, which the USGS is shutting down in early 2027. No key is needed.
  Texas Water Development Board, Water Data for Texas, for the reservoir's level and storage.

For each river gauge: the latest daily flow, what is normal for that day of the year (the median
of every other year on record), and the lowest daily flow on record. Ten years of daily values per
gauge are also saved to data/gauge_history/ for drought analysis.

    python scripts/build_stream_gauges.py
"""

import csv
import io
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from common import DATA, fetch, log_change, publish_features, record_layer

API = "https://api.waterdata.usgs.gov/ogcapi/v0/collections"
GAUGE_PAGE = "https://waterdata.usgs.gov/monitoring-location/USGS-{}/"
RESERVOIR_CSV = "https://www.waterdatafortexas.org/reservoirs/individual/gibbons-creek.csv"
RESERVOIR_PAGE = "https://www.waterdatafortexas.org/reservoirs/individual/gibbons-creek"
OUT = DATA / "stream_gauges.geojson"
HISTORY = DATA / "gauge_history"

FLOW = "00060"                   # USGS code for discharge, in cubic feet per second
DAILY_MEAN = "00003"
PAGE = 50000                     # most rows the service gives in one answer
HISTORY_YEARS = 10
MIN_YEARS_FOR_NORMAL = 10        # with fewer years than this, a median says little
STALE_DAYS = 14                  # no flow value for this long counts as not reporting
FULL_DAY_SHARE = 0.8             # a day needs this share of the usual number of readings to be averaged
READINGS_PER_REQUEST_DAYS = 120  # 5 minute readings: about 35,000 in this many days, under the row limit
RECHECK_DAYS = 7                 # fetch this much overlap, since recent readings get revised
STANDARD_TIME = timezone(timedelta(hours=-6))    # the USGS works out daily values in standard time
GALLONS_PER_ACRE_FOOT = 325851
# Navasota River diversion point in water right 5307. It falls inside the one-acre river parcel listed
# under WIT TECH LLC; the pump station itself is on the company's three-acre parcel about 0.4 miles east.
PUMP_STATION = (-96.166614, 30.65225)

# The Navasota River gauges that measure flow, upstream to downstream. No gauge on Gibbons Creek
# itself, or on the other creeks in the map's watersheds, measures flow; a few record water level only.
# daily: True where the USGS publishes daily values; False where only quarter-hour readings exist
# and the daily averages are worked out here.
GAUGES = [
    {"site": "08110500", "name": "Navasota River near Easterly", "daily": True,
     "where": "Far upstream, below Lake Limestone. The longest flow record on the river."},
    {"site": "08110800", "name": "Navasota River at Old San Antonio Road near Bryan", "daily": True,
     "where": "The nearest gauge upstream of the Navasota River pump station."},
    # This one publishes flow only while the river is up (so far, never below about 157 cubic feet per second),
    # so it has no low-flow record and no meaningful normal. Its level is reported all the time.
    {"site": "08111070", "name": "Navasota River at State Highway 6 near Navasota", "daily": False, "high_water_only": True,
     "where": "Downstream of the pump station and of Gibbons Creek."},
]
LEVEL = "00065"                  # USGS code for gauge height, in feet
RESERVOIR_SITE = "08111058"      # the USGS level gauge on the reservoir, used for its position

DRY, LOW, NORMAL, HIGH, WET = "Much below normal", "Below normal", "Normal", "Above normal", "Much above normal"
NOT_REPORTING, SHORT_RECORD = "Not reporting flow", "Record too short to say"
HIGH_WATER_ONLY = "Measures flow only in high water"


def rows(collection, **params):
    """Rows from one of the USGS collections, as dictionaries."""
    params = {"f": "csv", "skipGeometry": "true", "limit": PAGE, **params}
    found = list(csv.DictReader(io.StringIO(fetch(f"{API}/{collection}/items", params, timeout=300).decode("utf-8"))))
    if len(found) >= PAGE:
        sys.exit(f"The USGS returned {PAGE:,} rows for {params}, its limit, so some are missing. Nothing written.")
    return found


def location(site):
    data = json.loads(fetch(f"{API}/monitoring-locations/items", {"f": "json", "id": f"USGS-{site}"}))
    if not data["features"]:
        sys.exit(f"The USGS no longer lists gauge {site}. Nothing written.")
    return [round(c, 6) for c in data["features"][0]["geometry"]["coordinates"][:2]]


def daily_flows(site):
    """{date: cubic feet per second} for a gauge's whole record of published daily values."""
    found = rows("daily", monitoring_location_id=f"USGS-{site}", parameter_code=FLOW, statistic_id=DAILY_MEAN,
                 properties="time,value")
    return {date.fromisoformat(r["time"]): float(r["value"]) for r in found if r["value"] not in ("", None)}


def saved_flows(site):
    """{date: flow} from the gauge's history file, or {} if there is none yet."""
    path = HISTORY / f"{site}.json"
    if not path.exists():
        return {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    start = date.fromisoformat(saved["start"])
    return {start + timedelta(days=i): v for i, v in enumerate(saved["flow"]) if v is not None}


def daily_flows_from_readings(site, today):
    """The same, worked out as the average of each full day's readings (taken every 5 to 15 minutes).

    The readings run to a hundred thousand a year, so the whole record is only fetched the first
    time. After that the history file holds the daily averages and only recent readings are fetched.
    """
    flows = saved_flows(site)
    if flows:
        start = max(flows) - timedelta(days=RECHECK_DAYS)
    else:
        series = json.loads(fetch(f"{API}/time-series-metadata/items", {
            "f": "json", "monitoring_location_id": f"USGS-{site}", "parameter_code": FLOW, "limit": 50}))["features"]
        if not series:
            return {}
        start = min(datetime.fromisoformat(s["properties"]["begin"]).date() for s in series)
    by_day = defaultdict(dict)
    while start <= today:
        end = min(start + timedelta(days=READINGS_PER_REQUEST_DAYS), today + timedelta(days=1))
        for r in rows("continuous", monitoring_location_id=f"USGS-{site}", parameter_code=FLOW, properties="time,value",
                      datetime=f"{start}T06:00:00Z/{end}T06:00:00Z"):
            if r["value"] not in ("", None):
                when = datetime.fromisoformat(r["time"]).astimezone(STANDARD_TIME)
                by_day[when.date()][when] = float(r["value"])      # keyed by time: a reading on a request boundary arrives twice
        start = end
    if by_day:
        typical = statistics.median(len(readings) for readings in by_day.values())
        for day, readings in by_day.items():
            if len(readings) >= typical * FULL_DAY_SHARE:
                flows[day] = round(statistics.fmean(readings.values()), 2)
    return flows


def part_of_month(day):
    return f"{'early' if day.day <= 10 else 'mid' if day.day <= 20 else 'late'} {day:%B}"


def short(day):
    return f"{day:%b} {day.day}, {day.year}"


def number(value):
    """A flow as people would say it: 0.4, 13.7, 154, 12,300."""
    return f"{value:,.0f}" if value >= 100 else f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"


def miles_from_pump_station(lng, lat):
    """Straight-line miles, on a flat local grid that is exact enough at this scale."""
    import math
    dx = (lng - PUMP_STATION[0]) * 69.172 * math.cos(math.radians(PUMP_STATION[1]))
    dy = (lat - PUMP_STATION[1]) * 68.94
    return math.hypot(dx, dy)


def latest_level(site):
    """(feet, date) of the gauge's most recent water level reading, or (None, None)."""
    found = rows("latest-continuous", monitoring_location_id=f"USGS-{site}", parameter_code=LEVEL, properties="time,value")
    found = [r for r in found if r["value"] not in ("", None)]
    if not found:
        return None, None
    last = max(found, key=lambda r: r["time"])
    return float(last["value"]), datetime.fromisoformat(last["time"]).astimezone(STANDARD_TIME).date()


def high_water_feature(gauge, flows, coords, today):
    """A gauge that measures flow only while the river is up: say so, and give the level instead."""
    latest = max(flows)
    floor = min(flows.values())
    running = (today - latest).days <= 2
    level, level_day = latest_level(gauge["site"])
    props = {
        "name": gauge["name"],
        "description": (f"Flow on {latest:%b} {latest.day}: {number(flows[latest])} cubic feet per second" if running else
                        f"The river is below the level where this gauge measures flow. It last did on {short(latest)}"),
        "status": HIGH_WATER_ONLY,
        "Latest daily flow (cfs)": flows[latest] if running else None,
        "Date of that flow": latest.isoformat() if running else None,
        "River level (feet on the gauge)": level,
        "Date of that level": level_day.isoformat() if level_day else None,
        "Flow is measured": f"Only in high water. The lowest daily flow it has reported is {number(floor)} cubic feet per second, "
                            "so it cannot show how low the river gets",
        "Days with a flow value": f"{len(flows):,} since {min(flows).year}, worked out from the gauge's 5 minute readings",
        "Where": f"{gauge['where']} About {miles_from_pump_station(*coords):.0f} miles in a straight line from the pump station.",
        "USGS site number": gauge["site"],
        "url": GAUGE_PAGE.format(gauge["site"]),
        "link": "Open the USGS gauge page",
        "id": gauge["site"],
    }
    return {"type": "Feature", "properties": {k: v for k, v in props.items() if v is not None},
            "geometry": {"type": "Point", "coordinates": coords}}


def gauge_feature(gauge, flows, coords, today):
    if gauge.get("high_water_only"):
        return high_water_feature(gauge, flows, coords, today)
    latest = max(flows)
    value = flows[latest]
    # Every other year's flow on the same day of the year (February 29 borrows February 28)
    same_day = [v for d, v in flows.items()
                if d.year != latest.year and (d.month, d.day) == (latest.month, 28 if (latest.month, latest.day) == (2, 29) else latest.day)]
    years = len(same_day)
    median = statistics.median(same_day) if same_day else None
    percent = round(value / median * 100) if median else None
    lowest = min(flows.values())
    lowest_days = sorted(d for d, v in flows.items() if v == lowest)

    if (today - latest).days > STALE_DAYS:
        status = NOT_REPORTING
        summary = f"No flow reported since {short(latest)}, when it was {number(value)} cubic feet per second"
    else:
        summary = f"Flow on {latest:%b} {latest.day}: {number(value)} cubic feet per second"
        if years < MIN_YEARS_FOR_NORMAL:
            status = SHORT_RECORD
        else:
            below = sum(v < value for v in same_day) / years      # share of years that were lower on this day
            status = DRY if below < 0.10 else LOW if below < 0.25 else NORMAL if below <= 0.75 else HIGH if below <= 0.90 else WET
            if percent is not None:
                summary += f", about {percent:,}% of normal for {part_of_month(latest)}"

    props = {
        "name": gauge["name"],
        "description": summary,
        "Compared with normal": status,
        "status": status,            # picks the legend row; not shown in the pop-up
        "Latest daily flow (cfs)": value,
        "Date of that flow": latest.isoformat(),
        "Normal for that day (median, cfs)": round(median, 1) if median is not None else None,
        "Percent of normal": percent,
        "Years behind that normal": years,
        "Lowest daily flow on record (cfs)": lowest,
        "Date of the lowest flow": lowest_days[-1].isoformat() if len(lowest_days) == 1
        else f"{len(lowest_days):,} days, most recently {lowest_days[-1].isoformat()}",
        "Daily flow record": f"{min(flows).year} to {latest.year}, {len(flows):,} days",
        "Where": f"{gauge['where']} About {miles_from_pump_station(*coords):.0f} miles in a straight line from the pump station.",
        "USGS site number": gauge["site"],
        "url": GAUGE_PAGE.format(gauge["site"]),
        "link": "Open the USGS gauge page",
        "id": gauge["site"],
    }
    return {"type": "Feature", "properties": {k: v for k, v in props.items() if v is not None},
            "geometry": {"type": "Point", "coordinates": coords}}


def gallons(acre_feet):
    return f"{acre_feet * GALLONS_PER_ACRE_FOOT / 1e9:.1f} billion"


def reservoir_feature(coords):
    """Gibbons Creek Reservoir's latest level and storage, plus its daily record."""
    text = fetch(RESERVOIR_CSV).decode("utf-8")
    record = [r for r in csv.DictReader(line for line in io.StringIO(text) if not line.startswith("#"))
              if r["percent_full"] not in ("", None)]
    if not record:
        sys.exit("The Texas Water Development Board returned no data for Gibbons Creek Reservoir. Nothing written.")
    days = {date.fromisoformat(r["date"]): r for r in record}
    latest = max(days)
    now = days[latest]
    full = float(now["percent_full"])
    year_ago = days.get(latest - timedelta(days=365))
    lowest_day = min(days, key=lambda d: float(days[d]["percent_full"]))
    props = {
        "name": "Gibbons Creek Reservoir",
        "description": f"{full:.1f}% full on {short(latest)}",
        "status": "Reservoir level",
        "Percent full": full,
        "Water level (feet above sea level)": float(now["water_level"]),
        "Usable water stored (acre-feet)": int(float(now["conservation_storage"])),
        "Usable water stored (gallons)": gallons(float(now["conservation_storage"])),
        "Usable storage when full (acre-feet)": int(float(now["conservation_capacity"])),
        "Surface area (acres)": round(float(now["surface_area"])),
        "A year earlier": f"{float(year_ago['percent_full']):.1f}% full" if year_ago else None,
        f"Lowest since {min(days).year}": f"{float(days[lowest_day]['percent_full']):.1f}% full on {short(lowest_day)}",
        "Date of these readings": latest.isoformat(),
        "url": RESERVOIR_PAGE,
        "link": "Open the Water Data for Texas page",
        "id": "gibbons-creek-reservoir",
    }
    feature = {"type": "Feature", "properties": {k: v for k, v in props.items() if v is not None},
               "geometry": {"type": "Point", "coordinates": coords}}
    return feature, days


def write_history(name, payload, values_by_day, columns, today, whole_record=False):
    """One compact file per gauge: a start date, then one value per day (null where there is none).

    whole_record keeps every year, for a gauge whose daily averages exist nowhere but this file.
    """
    start = min(values_by_day) if whole_record else max(min(values_by_day), today.replace(year=today.year - HISTORY_YEARS))
    end = max(values_by_day)
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    payload = {**payload, "start": start.isoformat(), "end": end.isoformat(),
               "note": "One value per day from start to end, in order. null means no value that day."}
    for column, pick in columns.items():
        payload[column] = [pick(values_by_day[d]) if d in values_by_day else None for d in span]
    HISTORY.mkdir(exist_ok=True)
    path = HISTORY / f"{name}.json"
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return path, len(span)


def log_shifts(old, new):
    """Log what a reader would want to hear about, not each day's new number."""
    before = {f["properties"]["id"]: f["properties"] for f in old}
    for p in (f["properties"] for f in new):
        was = before.get(p["id"])
        key, low = "status","Lowest daily flow on record (cfs)"
        if not was or low not in p:
            continue
        if p[key] != was.get(key) and DRY in (p[key], was.get(key)):
            log_change("gauges", f"Stream gauges: {p['name']} is now {p[key].lower()} for the time of year (was {str(was.get(key)).lower()})", p["url"])
        elif p[key] != was.get(key) and NOT_REPORTING in (p[key], was.get(key)):
            log_change("gauges", f"Stream gauges: {p['name']} has {'stopped' if p[key] == NOT_REPORTING else 'resumed'} reporting flow", p["url"])
        if was.get(low) is not None and p[low] < was[low]:
            log_change("gauges", f"Stream gauges: {p['name']} set a new record low flow of {number(p[low])} cubic feet per second", p["url"])


def main():
    today = date.today()
    old = json.loads(OUT.read_text(encoding="utf-8"))["features"] if OUT.exists() else None
    features, report = [], []

    for gauge in GAUGES:
        flows = daily_flows(gauge["site"]) if gauge["daily"] else daily_flows_from_readings(gauge["site"], today)
        if not flows:
            sys.exit(f"The USGS returned no flow values for gauge {gauge['site']}. Nothing written.")
        feature = gauge_feature(gauge, flows, location(gauge["site"]), today)
        features.append(feature)
        about = {"site": gauge["site"], "name": gauge["name"], "unit": "cubic feet per second", "source": GAUGE_PAGE.format(gauge["site"])}
        if gauge.get("high_water_only"):
            about["caution"] = "This gauge measures flow only in high water. A null here usually means the river was too low to measure, not that data is missing."
        path, days = write_history(gauge["site"], about, flows, {"flow": lambda v: v}, today, whole_record=not gauge["daily"])
        report.append((feature["properties"], f"{path.name}, {days:,} days, {path.stat().st_size / 1024:.0f} KB"))

    reservoir, levels = reservoir_feature(location(RESERVOIR_SITE))
    features.append(reservoir)
    path, days = write_history("gibbons_creek_reservoir", {
        "name": "Gibbons Creek Reservoir", "units": {"percent_full": "percent", "water_level": "feet above sea level",
                                                     "conservation_storage": "acre-feet"},
        "source": RESERVOIR_PAGE}, levels,
        {c: (lambda r, c=c: float(r[c]) if r[c] not in ("", None) else None)
         for c in ("percent_full", "water_level", "conservation_storage")}, today)

    size = publish_features("gauges", "Stream gauges", OUT, features, f"{API}/daily/items", ("gauge", "gauges"),
                            key="id", log_changes=old is None)
    record_layer("reservoir", RESERVOIR_CSV, 1)
    if old is not None:
        log_shifts(old, features)

    print(f"Wrote {len(features)} features to {OUT.name} ({size / 1024:.1f} KB).")
    for p, history in report:
        low = "Lowest daily flow on record (cfs)"
        print(f"  {p['name']} ({p['USGS site number']})\n    {p['description']}\n    {p['status']}"
              + (f"; lowest on record {p[low]} ({p['Date of the lowest flow']}); {p['Daily flow record']}" if low in p else "")
              + f"\n    history: {history}")
    print(f"  {reservoir['properties']['name']}: {reservoir['properties']['description']}\n"
          f"    history: {path.name}, {days:,} days, {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
