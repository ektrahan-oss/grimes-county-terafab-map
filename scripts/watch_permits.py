"""Watch state records for permits tied to the Terafab project.

Six public sources are checked:

  TCEQ Central Registry   Every Grimes County site registered to SpaceX, and each permit on it.
                          Also any site or customer named after Terafab or WIT Tech.
  TCEQ air permits        New Source Review projects in Grimes County from the last year.
  TDLR project registry   Building projects registered in Grimes County in the last year.
  Railroad Commission     Pipeline (T-4) permits in Grimes County, and any pipeline operator
                          named after the project anywhere in Texas.
  TxDOT project tracker   State road projects within about two miles of the reinvestment zone.
  Commissioners Court     Agenda items about permits, and plat, utility or road items that
                          name the project or the roads at the site.

SpaceX's own records are always tracked. Contractors file under their own names, so air permits
and building projects are also tracked when their name or location points at the site (see
ENTITY_WORDS and SITE_WORDS). Everything else in the county is ignored.

Not covered, because no public database exists: TxDOT driveway and utility permits, and the
county's own development, floodplain and septic permits. Those need a public information request.

What has been seen is kept in data/permit_watch.json. New items and status changes are logged in
data/changes.json with a link. The first run only records what is already there.

    python scripts/watch_permits.py
"""

import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

from common import COUNTY_NAME, DATA, USER_AGENT, fetch, fetch_json, log_change, record_layer

REGISTRY = "https://www15.tceq.texas.gov/crpub/index.cfm"
SPACEX_CUSTOMER = "CN602867657"                  # Space Exploration Technologies Corp.
REGISTRY_NAMES = ["TERAFAB", "WIT TECH"]         # other names to look for among sites and customers

AIR = "https://www2.tceq.texas.gov/airperm/index.cfm"
TDLR = "https://www.tdlr.texas.gov/TABS/Search"
TDLR_COUNTY_CODE = "2093"                        # Grimes, in TDLR's own numbering
LOOK_BACK_DAYS = 365

RRC = "https://gis.rrc.texas.gov/server/rest/services/rrc_public/RRC_Public_Viewer_Srvs/MapServer"
TXDOT = "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/TxDOT_Projects_Info/FeatureServer/0/query"
TXDOT_MARGIN_DEGREES = 0.03                      # about two miles around the reinvestment zone
ROUTINE_ROAD_WORK = {"seal coat", "profile markings", "preventive maintenance"}

AGENDA_API = "https://grimescotx.api.civicclerk.com/v1"
AGENDA_LOOK_BACK_DAYS = 45

ENTITY_WORDS = ["SPACEX", "SPACE EXPLORATION", "TERAFAB", "WIT TECH"]
SITE_WORDS = ["GIBBONS CREEK", "FM 244", "FM 171", "CARLOS"]     # roads and places at the site

STATE = DATA / "permit_watch.json"
MIN_FOR_SHORTFALL_CHECK = 8      # with at least this many items on file for a source...
MAX_SHORTFALL = 0.25             # ...losing more than this share at once is treated as a bad response


def post(url, fields, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(),
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def clean(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def table_rows(page):
    """Each table row on a page as a list of cell texts."""
    return [[clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S)]


def matches(text, words):
    flat = re.sub(r"[\s\-]+", " ", text.upper())
    return [w for w in words if w in flat]


# ---- TCEQ Central Registry ----

def registry_site(rn):
    """Permits and registrations on one regulated entity."""
    url = f"{REGISTRY}?fuseaction=regent.validateRE&re_ref_num_txt={rn}"
    rows = table_rows(fetch(url).decode("utf-8", "replace"))
    return url, [r for r in rows if len(r) == 4 and r[1] in ("PERMIT", "REGISTRATION", "ACCOUNT NUMBER", "AUTHORIZATION", "LICENSE", "ID NUMBER")]


def check_registry():
    found = {}
    first = post(REGISTRY, {"_fuseaction=cust.validateCust": "Search", "pr_ref_num_txt": SPACEX_CUSTOMER, "pr_name_txt": ""})
    pages, more = [first], re.search(r'href="([^"]*CurrentPage=)2(&[^"]*)"', first)
    page_no = 2
    while more and f"CurrentPage={page_no}" in pages[-1]:
        link = html.unescape(f"{more.group(1)}{page_no}{more.group(2)}")
        pages.append(fetch(urllib.parse.urljoin(REGISTRY, link.replace(" ", "%20"))).decode("utf-8", "replace"))
        page_no += 1
    sites = [r for p in pages for r in table_rows(p) if len(r) >= 5 and re.fullmatch(r"RN\d{9}", r[0])]
    if not sites:
        raise RuntimeError("the TCEQ registry returned no sites for SpaceX's customer number")
    for rn, name, county, location, *_ in sites:
        if county.upper() != COUNTY_NAME.upper():
            continue
        url, permits = registry_site(rn)
        found[f"tceq-site:{rn}"] = {"what": f"SpaceX site registered with TCEQ, {name.title()} ({location.capitalize()})",
                                    "status": "listed", "link": url}
        for program, id_type, number, status in permits:
            found[f"tceq-permit:{rn}:{number}"] = {
                "what": f"TCEQ {program.lower()} {id_type.lower()} {number} for SpaceX's {name.title()}", "status": status.lower(), "link": url}

    for name in REGISTRY_NAMES:
        site_search = post(REGISTRY, {"_fuseaction=regent.validateRE": "Search", "re_name_txt": name, "cnty_name": COUNTY_NAME.upper(),
                                      "re_ref_num_txt": "", "pgm_area": "", "addn_num_txt": "", "addn_id_status_cd": "",
                                      "deliv_txt": "", "city_name": "", "zip_cd": ""})
        customer_search = post(REGISTRY, {"_fuseaction=cust.validateCust": "Search", "pr_ref_num_txt": "", "pr_name_txt": name})
        for kind, page in (("site", site_search), ("customer", customer_search)):
            ids = sorted(set(re.findall(r"\b(?:RN|CN)\d{9}\b", clean(page)))) if "No results were found" not in page else []
            for ref in ids[:10]:
                found[f"tceq-name:{ref}"] = {"what": f"TCEQ {kind} record matching \"{name.title()}\", {ref}", "status": "listed",
                                             "link": f"{REGISTRY}?fuseaction=regent.RNSearch"}
    return found


# ---- TCEQ air permits ----

def form_defaults(page):
    """A form's fields as a browser would send them untouched."""
    form = re.search(r"<form[^>]*>(.*?)</form>", page, re.S).group(1)
    fields = {}
    for tag in re.findall(r"<input[^>]*>", form):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        kind = attrs.get("type", "text")
        if "name" not in attrs or kind in ("submit", "image", "button", "reset"):
            continue
        if kind in ("radio", "checkbox") and "checked" not in tag:
            continue
        fields[attrs["name"]] = attrs.get("value", "")
    for name, body in re.findall(r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', form, re.S):
        chosen = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', body) or re.search(r'<option[^>]*value="([^"]*)"', body)
        fields[name] = chosen.group(1) if chosen else ""
    return fields


def check_air():
    fields = form_defaults(fetch(AIR + "?fuseaction=airpermits.start").decode("utf-8", "replace"))
    today = date.today()
    fields.update({"loc_cnty_name": COUNTY_NAME.upper(), "date_option": "rcv_dt", "proj_status_txt": "ALL",
                   "date_range_from": (today - timedelta(days=LOOK_BACK_DAYS)).strftime("%m/%d/%Y"),
                   "date_range_to": today.strftime("%m/%d/%Y"),
                   "_fuseaction=airpermits.validate_search_criteria.x": "20", "_fuseaction=airpermits.validate_search_criteria.y": "10"})
    page = post(AIR, fields)
    if "Air Permitting Actions for" not in page:
        raise RuntimeError("the TCEQ air permit search did not return a results page")
    found = {}
    for r in table_rows(page):
        if len(r) < 19 or r[0] != "NSR":
            continue
        permit, project, customer, received, status, project_name, location = r[1], r[4], r[5], r[9], r[12], r[13], r[15]
        entity, near = matches(customer, ENTITY_WORDS), matches(location, SITE_WORDS)
        if not (entity or near):
            continue
        whose = "SpaceX air permit" if entity else "Air permit near the site"
        found[f"air:{project}"] = {
            "what": f"{whose}: {customer.title()}, {project_name.lower()} (permit {permit}, received {received}). Location: {location.capitalize()}",
            "status": status.lower(), "link": AIR + "?fuseaction=airpermits.start"}
    return found


# ---- TDLR building projects ----

def check_tdlr():
    since = (date.today() - timedelta(days=LOOK_BACK_DAYS)).strftime("%m/%d/%Y")
    fields = {"draw": 1, "start": 0, "length": 100, "order[0][column]": 3, "order[0][dir]": "desc",
              "LocationCounty": TDLR_COUNTY_CODE, "RegistrationDateBegin": since, "RegistrationDateEnd": "",
              "ProjectName": "", "ProjectNumber": "", "ProjectStatus": "", "DateBegin": "", "DateEnd": "", "OwnerName": "",
              "FacilityName": "", "ArchitectName": "", "LocationAddress": "", "LocationCity": "", "RASNumber": ""}
    data = json.loads(post(f"{TDLR}/SearchProjects", fields, {"X-Requested-With": "XMLHttpRequest"}))
    found = {}
    for p in data["data"]:
        names = f"{p.get('ProjectName', '')} {p.get('FacilityName', '')}"
        if not (matches(names, ENTITY_WORDS) or matches(names, SITE_WORDS)):
            continue
        cost = f", estimated ${p['EstimatedCost']:,.0f}" if p.get("EstimatedCost") else ""
        found[f"tdlr:{p['ProjectNumber']}"] = {
            "what": f"Building project registered with TDLR: {p['ProjectName']}{cost}", "status": "registered",
            "link": f"{TDLR}/Project/{p['ProjectNumber']}"}
    return found, len(data["data"])


# ---- Railroad Commission pipeline permits ----

def check_rrc():
    layer = None
    for candidate in fetch_json(RRC, {"f": "json"})["layers"]:
        if candidate["name"] == "Pipelines" and \
                "transmission only" not in fetch_json(f"{RRC}/{candidate['id']}", {"f": "json"}).get("description", "").lower():
            layer = f"{RRC}/{candidate['id']}/query"
    if not layer:
        raise RuntimeError("the Railroad Commission service no longer has a full Pipelines layer")

    expected = json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("layers", {}).get("pipelines", {}).get("feature_count")
    now = fetch_json(layer, {"where": f"COUNTY_NAME='{COUNTY_NAME.upper()}'", "returnCountOnly": "true", "f": "json"})["count"]
    if expected and now < expected * 0.9:
        # The Commission reloads this layer from time to time, and partway through it returns only part of the data
        raise RuntimeError(f"the service has {now} pipeline segments for the county where the map's file has {expected}; "
                           "it looks mid-update, so nothing was changed")

    def permits(where):
        rows = fetch_json(layer, {"where": where, "outFields": "T4PERMIT,OPERATOR,COMMODITY_DESCRIPTION,STATUS,COUNTY_NAME",
                                  "returnDistinctValues": "true", "returnGeometry": "false", "f": "json"})["features"]
        grouped = {}
        for r in (f["attributes"] for f in rows):
            key = (r["T4PERMIT"], " ".join((r["OPERATOR"] or "").split()))
            entry = grouped.setdefault(key, {"commodity": set(), "status": set(), "county": set()})
            entry["commodity"].add((r["COMMODITY_DESCRIPTION"] or "").strip().lower())
            entry["status"].add((r["STATUS"] or "").strip().lower())
            entry["county"].add((r["COUNTY_NAME"] or "").title())
        return grouped

    found = {}
    link = "https://www.rrc.texas.gov/resource-center/research/gis-viewer/"
    for (permit, operator), info in permits(f"COUNTY_NAME='{COUNTY_NAME.upper()}'").items():
        found[f"rrc:{permit}:{operator}"] = {
            "what": f"Railroad Commission pipeline permit T-4 {permit} in Grimes County, {operator} ({', '.join(sorted(info['commodity']))})",
            "status": " and ".join(sorted(info["status"])), "link": link}
    named = " OR ".join(f"UPPER(OPERATOR) LIKE '%{w}%'" for w in ENTITY_WORDS)
    for (permit, operator), info in permits(named).items():
        found[f"rrc:{permit}:{operator}"] = {
            "what": f"Railroad Commission pipeline permit T-4 {permit} held by {operator}, in {', '.join(sorted(info['county']))} County",
            "status": " and ".join(sorted(info["status"])), "link": link}
    return found


# ---- TxDOT road projects near the site ----

def zone_bounds():
    """West, south, east, north of the reinvestment zone, from the map's own file."""
    def flat(coords):
        if isinstance(coords[0], (int, float)):
            yield coords
        else:
            for c in coords:
                yield from flat(c)
    zone = json.loads((DATA / "reinvestment_zone.geojson").read_text(encoding="utf-8"))
    points = [p for f in zone["features"] for p in flat(f["geometry"]["coordinates"])]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def check_txdot():
    west, south, east, north = zone_bounds()
    m = TXDOT_MARGIN_DEGREES
    rows = fetch_json(TXDOT, {
        "where": "1=1", "geometry": f"{west - m},{south - m},{east + m},{north + m}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false", "f": "json",
        "outFields": "CONTROL_SECT_JOB,HWY_NBR,LIMITS_FROM,LIMITS_TO,PT_PHASE,TYPE_OF_WORK,EST_CONSTRUCTION_COST"})["features"]
    found = {}
    for r in (f["attributes"] for f in rows):
        if (r["TYPE_OF_WORK"] or "").strip().lower() in ROUTINE_ROAD_WORK:
            continue
        limits = r["LIMITS_FROM"] if (r["LIMITS_TO"] or ".").strip() == "." else f"{r['LIMITS_FROM']} to {r['LIMITS_TO']}"
        cost = f", estimated ${r['EST_CONSTRUCTION_COST']:,.0f}" if r.get("EST_CONSTRUCTION_COST") else ""
        found[f"txdot:{r['CONTROL_SECT_JOB']}"] = {
            "what": f"TxDOT project near the site: {r['HWY_NBR']}, {(r['TYPE_OF_WORK'] or 'work').lower()}, {limits}{cost}",
            "status": (r["PT_PHASE"] or "not stated").lower(), "link": "https://www.txdot.gov/projects/project-tracker.html"}
    return found


# ---- Commissioners Court agenda items ----

AGENDA_ITEM = re.compile(r"\n\s*\d{1,2}\. (.*?)(?=\n\s*\d{1,2}\. |\n\s*[A-Z][A-Z ]{6,}:|\Z)", re.S)
PERMIT_ITEM = re.compile(r"permit", re.I)
STANDING_ITEM = re.compile(r"County Judge:")   # the liaison list printed on every agenda
LAND_ITEM = re.compile(r"\bplat\b|replat|variance|utility|pipeline|right[- ]of[- ]way|easement|road", re.I)


def check_county():
    since = (date.today() - timedelta(days=AGENDA_LOOK_BACK_DAYS)).isoformat()
    query = urllib.parse.quote(f"$filter=startDateTime gt {since}T00:00:00Z&$orderby=startDateTime desc", safe="$=&")
    found, events, url = {}, [], f"{AGENDA_API}/Events?{query}"
    while url:                                  # the portal hands out 15 meetings at a time
        page = fetch_json(url)
        events += page["value"]
        url = page.get("@odata.nextLink")
    for event in events:
        for f in event.get("publishedFiles") or []:
            if f.get("type") != "Agenda":
                continue
            stream = f"{AGENDA_API}/Meetings/GetMeetingFileStream(fileId={f['fileId']},plainText="
            text = re.sub(r"[ \t]+", " ", fetch(stream + "true)").decode("utf-8", "replace"))
            meeting = event["startDateTime"][:10]
            for item in (" ".join(m.group(1).split()) for m in AGENDA_ITEM.finditer(text)):
                about_site = matches(item, ENTITY_WORDS) or matches(item, SITE_WORDS)
                if STANDING_ITEM.match(item) or not (PERMIT_ITEM.search(item) or (about_site and LAND_ITEM.search(item))):
                    continue
                digest = hashlib.sha1(f"{meeting} {item}".encode("utf-8")).hexdigest()[:12]
                found[f"county:{digest}"] = {"what": f"Commissioners Court agenda item for {meeting}: {item[:260]}",
                                             "status": "on the agenda", "link": stream + "false)"}
    return found


# (prefix, name, check, whether to log an item that disappears). Windowed sources just age out.
SOURCES = [
    ("tceq", "TCEQ registry", check_registry, True),
    ("air", "TCEQ air permits", check_air, False),
    ("tdlr", "TDLR project registry", lambda: check_tdlr()[0], False),
    ("rrc", "Railroad Commission pipeline permits", check_rrc, True),
    ("txdot", "TxDOT project tracker", check_txdot, True),
    ("county", "Commissioners Court agendas", check_county, False),
]


def main():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    # A source's first run only records what is there. Files written before this list existed held three sources.
    recorded = state.pop("_recorded", ["tceq", "air", "tdlr"] if state else [])
    seen, changes, failed = state, [], []

    for prefix, name, check, log_removed in SOURCES:
        try:
            found = check()
        except Exception as e:                  # one source being down should not hide the others
            failed.append(f"{name}: {e}")
            continue
        first_time = prefix not in recorded
        on_file = [k for k in seen if k.split(":")[0].split("-")[0] == prefix]
        missing = [k for k in on_file if k not in found]
        if len(on_file) >= MIN_FOR_SHORTFALL_CHECK and len(missing) > len(on_file) * MAX_SHORTFALL:
            # Seen when the Railroad Commission's service was reloading its data and returned a fraction of it
            failed.append(f"{name}: returned {len(found)} items but {len(missing)} of the {len(on_file)} on file were missing. "
                          "The source looks incomplete, so nothing was changed")
            continue
        for key, item in found.items():
            old = seen.get(key)
            if old is None and not first_time:
                changes.append((f"Permits: new, {item['what']}. Status: {item['status']}", item["link"]))
            elif old and old["status"] != item["status"]:
                changes.append((f"Permits: status changed from {old['status']} to {item['status']}, {item['what']}", item["link"]))
            seen[key] = {**item, "first_seen": (old or {}).get("first_seen", date.today().isoformat())}      # also clears missing_since
        # An item has to be missing on two different days before it is dropped, in case a source hiccups
        today = date.today().isoformat()
        for key in [k for k in seen if k.split(":")[0].split("-")[0] == prefix and k not in found]:
            if seen[key].setdefault("missing_since", today) == today:
                continue
            gone = seen.pop(key)
            if log_removed:
                changes.append((f"Permits: no longer listed, {gone['what']}", gone["link"]))
        if first_time:
            recorded.append(prefix)
            print(f"  {name}: first run, recorded {len(found)} without logging them.")

    STATE.write_text(json.dumps({"_recorded": sorted(recorded), **dict(sorted(seen.items()))}, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    for summary, link in changes:
        log_change("permits", summary, link)
        print(f"  Change logged: {summary}")
    if not changes:
        print("  No change since the last run.")

    record_layer("permits", REGISTRY, len(seen), complete=not failed)
    print(f"Tracking {len(seen)} records:")
    for prefix, name, _, _ in SOURCES:
        items = [v for k, v in seen.items() if k.split(":")[0].split("-")[0] == prefix]
        print(f"  {len(items):>3}  {name}")
    if failed:
        sys.exit("Could not check: " + "; ".join(failed))


if __name__ == "__main__":
    main()
