"""Watch state records for permits tied to the Terafab project.

Three public databases are checked:

  TCEQ Central Registry   Every Grimes County site registered to SpaceX, and each permit on it.
                          Also any site or customer named after Terafab or WIT Tech.
  TCEQ air permits        New Source Review projects in Grimes County from the last year.
  TDLR project registry   Building projects registered in Grimes County in the last year.

SpaceX's own records are always tracked. Contractors file under their own names, so air permits
and building projects are also tracked when their name or location points at the site (see
ENTITY_WORDS and SITE_WORDS). Everything else in the county is ignored.

What has been seen is kept in data/permit_watch.json. New items and status changes are logged in
data/changes.json with a link. The first run only records what is already there.

    python scripts/watch_permits.py
"""

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

from common import COUNTY_NAME, DATA, USER_AGENT, fetch, log_change, record_layer

REGISTRY = "https://www15.tceq.texas.gov/crpub/index.cfm"
SPACEX_CUSTOMER = "CN602867657"                  # Space Exploration Technologies Corp.
REGISTRY_NAMES = ["TERAFAB", "WIT TECH"]         # other names to look for among sites and customers

AIR = "https://www2.tceq.texas.gov/airperm/index.cfm"
TDLR = "https://www.tdlr.texas.gov/TABS/Search"
TDLR_COUNTY_CODE = "2093"                        # Grimes, in TDLR's own numbering
LOOK_BACK_DAYS = 365

ENTITY_WORDS = ["SPACEX", "SPACE EXPLORATION", "TERAFAB", "WIT TECH"]
SITE_WORDS = ["GIBBONS CREEK", "FM 244", "FM 171", "CARLOS"]     # roads and places at the site

STATE = DATA / "permit_watch.json"


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


def main():
    baseline = not STATE.exists()
    seen = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    changes, failed, counts = [], [], {}

    def tdlr_only():
        found, total = check_tdlr()
        counts["TDLR projects in the county this year"] = total
        return found

    for prefix, name, check in [("tceq", "TCEQ registry", check_registry), ("air", "TCEQ air permits", check_air),
                                ("tdlr", "TDLR project registry", tdlr_only)]:
        try:
            found = check()
        except Exception as e:                  # one database being down should not hide the others
            failed.append(f"{name}: {e}")
            continue
        for key, item in found.items():
            old = seen.get(key)
            if old is None and not baseline:
                changes.append((f"Permits: new, {item['what']}. Status: {item['status']}", item["link"]))
            elif old and old["status"] != item["status"]:
                changes.append((f"Permits: status changed from {old['status']} to {item['status']}, {item['what']}", item["link"]))
            seen[key] = {**item, "first_seen": (old or {}).get("first_seen", date.today().isoformat())}
        for key in [k for k in seen if k.startswith(prefix) and k not in found]:
            gone = seen.pop(key)
            if not key.startswith("air"):       # air projects simply age out of the one-year window
                changes.append((f"Permits: no longer listed, {gone['what']}", gone["link"]))

    STATE.write_text(json.dumps(dict(sorted(seen.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for summary, link in changes:
        log_change("permits", summary, link)
        print(f"  Change logged: {summary}")
    if not changes:
        print("  First run: recorded what is already on file." if baseline else "  No change since the last run.")

    record_layer("permits", REGISTRY, len(seen))
    print(f"Tracking {len(seen)} permit records.")
    for item in seen.values():
        print(f"  [{item['status']}] {item['what']}")
    for label, n in counts.items():
        print(f"  ({n} {label}; none tracked unless listed above)")
    if failed:
        sys.exit("Could not check: " + "; ".join(failed))


if __name__ == "__main__":
    main()
