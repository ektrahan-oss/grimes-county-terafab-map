"""Pull population and housing figures by census tract into data/population_housing.geojson.

Source: U.S. Census Bureau, American Community Survey 5-year estimates, for Grimes County and the
six counties around it. The figures come from the Bureau's downloadable summary files rather than
its data API, which now needs a key; the numbers are the same. Tract shapes come from the Bureau's
TIGERweb service for the same survey year.

A new 5-year release comes out once a year, usually in December. Each run checks for one and does
nothing more if the file already holds the newest, so it is cheap to leave in the monthly refresh.

    python scripts/fetch_population_housing.py           only rebuilds if there is a newer release
    python scripts/fetch_population_housing.py --force   rebuild from the newest release regardless
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import date

import geopandas as gpd
import shapely

from common import CACHE, DATA, USER_AGENT, WORK_CRS, fetch, fetch_json, publish_geojson

FILES = "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/acsdt5y{year}-{table}.dat"
TRACTS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS{year}/MapServer"
TRACT_LAYER = "Census Tracts"
ABOUT = "https://www.census.gov/programs-surveys/acs"
STATE = "48"
COUNTIES = {"185": "Grimes", "041": "Brazos", "313": "Madison", "339": "Montgomery", "471": "Walker", "473": "Waller", "477": "Washington"}
SIMPLIFY_FEET = 150
OLDEST_TO_TRY = 3                # how many years back to look for the newest release
OUT = DATA / "population_housing.geojson"

# table -> {column: (name used here, keep its margin of error?)}
TABLES = {
    "b01003": {"B01003_E001": "population"},
    "b25002": {"B25002_E001": "units", "B25002_E003": "vacant"},
    "b25064": {"B25064_E001": "rent", "B25064_M001": "rent_margin"},
    "b25077": {"B25077_E001": "value", "B25077_M001": "value_margin"},
    "b19013": {"B19013_E001": "income", "B19013_M001": "income_margin"},
}


def exists(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False


def newest_release():
    """The latest end year with a published 5-year summary file."""
    this_year = date.today().year
    for year in range(this_year, this_year - OLDEST_TO_TRY - 1, -1):
        if exists(FILES.format(year=year, table="b01003")):
            return year
    sys.exit(f"No American Community Survey 5-year files found for {this_year - OLDEST_TO_TRY} to {this_year}. "
             "The Census Bureau may have moved them. Nothing written.")


def number(text):
    """A whole number, or None for the Bureau's blanks and its negative codes for 'not available'."""
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value >= 0 else None


def table(year, name, columns):
    """{tract id: {our name: value}} for the tracts in COUNTIES. Each file covers the whole country."""
    cached = CACHE / "acs" / f"{year}-{name}.dat"
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(fetch(FILES.format(year=year, table=name), timeout=600))
    wanted = tuple(f"1400000US{STATE}{county}" for county in COUNTIES)     # 140 is the Bureau's code for a tract
    found = {}
    with cached.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("|")
        missing = [c for c in columns if c not in header]
        if missing:
            sys.exit(f"The {year} file for table {name} has no column {missing[0]}. Its layout may have changed. Nothing written.")
        for line in f:
            if line.startswith(wanted):
                cells = dict(zip(header, line.rstrip("\n").split("|")))
                found[cells["GEO_ID"][9:]] = {ours: number(cells[theirs]) for theirs, ours in columns.items()}
    return found


def tract_shapes(year):
    service = TRACTS.format(year=year)
    layer = next((l["id"] for l in fetch_json(service, {"f": "json"})["layers"] if l["name"] == TRACT_LAYER), None)
    if layer is None:
        sys.exit(f"The Census Bureau's {year} boundary service has no layer named {TRACT_LAYER}. Nothing written.")
    url = f"{service}/{layer}/query"
    counties = ",".join(f"'{c}'" for c in COUNTIES)
    features = fetch_json(url, {"where": f"STATE='{STATE}' AND COUNTY IN ({counties})", "outFields": "GEOID,COUNTY,BASENAME",
                                "outSR": 4326, "f": "geojson"}, timeout=300)["features"]
    if not features:
        sys.exit("The Census Bureau returned no tract shapes. Nothing written.")
    return gpd.GeoDataFrame.from_features(features, crs=4326), url


def main():
    year = newest_release()
    period = f"{year - 4} to {year}"
    if OUT.exists() and "--force" not in sys.argv:
        have = json.loads(OUT.read_text(encoding="utf-8"))["features"][0]["properties"].get("Survey years")
        if have == period:
            print(f"Already holds the newest release ({period}). Nothing to do.")
            return

    figures = {}
    for name, columns in TABLES.items():
        for tract, values in table(year, name, columns).items():
            figures.setdefault(tract, {}).update(values)
    tracts, shapes_url = tract_shapes(year)
    unmatched = sorted(set(tracts["GEOID"]) - set(figures))
    if len(unmatched) > len(tracts) * 0.05:
        sys.exit(f"{len(unmatched)} of {len(tracts)} tracts have no figures, which looks like a mismatch between the "
                 "boundaries and the survey files. Nothing written.")

    tracts = tracts.to_crs(WORK_CRS)
    try:                                         # simplify all tracts together so shared borders stay shared
        tracts["geometry"] = shapely.coverage_simplify(tracts.geometry.values, SIMPLIFY_FEET)
    except shapely.errors.GEOSException:
        tracts["geometry"] = tracts.geometry.simplify(SIMPLIFY_FEET, preserve_topology=True)

    rows = []
    for t in tracts.itertuples():
        f = figures.get(t.GEOID, {})
        units, vacant = f.get("units"), f.get("vacant")
        rows.append({
            "name": f"Census tract {t.BASENAME}",
            "description": f"{COUNTIES[t.COUNTY]} County",
            "Population": f.get("population"),
            "Housing units": units,
            "Vacant housing units": vacant,
            "Vacancy rate (%)": round(vacant / units * 100, 1) if units else None,
            "Median gross rent ($ a month)": f.get("rent"),
            "Median gross rent ($ a month) margin": f.get("rent_margin") if f.get("rent") is not None else None,
            "Median home value ($)": f.get("value"),
            "Median home value ($) margin": f.get("value_margin") if f.get("value") is not None else None,
            "Median household income ($ a year)": f.get("income"),
            "Median household income ($ a year) margin": f.get("income_margin") if f.get("income") is not None else None,
            "Survey years": period,
            "id": t.GEOID,
            "geometry": t.geometry,
        })
    out = gpd.GeoDataFrame(rows, crs=WORK_CRS).sort_values("id").reset_index(drop=True)

    size = publish_geojson("census", "Population and housing", OUT, out, FILES.format(year=year, table="b01003"),
                           ("census tract", "census tracts"), key="id")
    print(f"American Community Survey {period}. Wrote {len(out)} tracts to {OUT.name} ({size / 1024:.0f} KB). Shapes from {shapes_url}")
    by_county = out.groupby("description")
    for county, group in by_county:
        print(f"  {county:<18} {len(group):>3} tracts  population {int(group['Population'].sum()):>9,}  "
              f"housing units {int(group['Housing units'].sum()):>8,}  vacant {group['Vacant housing units'].sum() / group['Housing units'].sum():.1%}")
    for column in ("Median gross rent ($ a month)", "Median home value ($)", "Median household income ($ a year)"):
        print(f"  {column}: {out[column].isna().sum()} tracts with no figure; range {out[column].min():,.0f} to {out[column].max():,.0f}")
    if unmatched:
        print(f"  {len(unmatched)} tracts have no survey figures: {', '.join(unmatched)}")


if __name__ == "__main__":
    main()
