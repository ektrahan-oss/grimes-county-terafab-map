"""Watch Texas company records for entities tied to the Terafab project. Writes data/entities.json.

Sources, both free and public, from the Texas Comptroller of Public Accounts:
  Active Franchise Taxpayers on data.texas.gov (every company set up for franchise tax in Texas)
  Franchise Tax Account Status lookup, for a company's registered agent and registered office

The watch looks for the project's known companies, any company with Terafab in its name, and any
company using the same mailing address as SpaceX or as the company buying the land. New companies
and changes are logged to data/changes.json.

What the free data cannot do: it does not list registered agents, so companies cannot be found by
agent. The project's companies use a commercial agent shared with many thousands of others, so that
search would say little anyway. The Secretary of State's paid search (SOSDirect) is not used.

Only companies are recorded. Officers' names are never kept, a registered agent who is a person is
not named, and street addresses are kept only where the address is the reason for the match.

    python scripts/watch_entities.py
"""

import json
import re
import sys
from datetime import date

from common import DATA, fetch_json, log_change, record_layer

DATASET = "https://data.texas.gov/resource/9cir-efmm.json"
DATASET_PAGE = "https://data.texas.gov/dataset/Active-Franchise-Taxpayers/9cir-efmm"
DETAIL = "https://comptroller.texas.gov/data-search/franchise-tax/{}"
ACCOUNT_PAGE = "https://comptroller.texas.gov/taxes/franchise/account-status/search/{}"
OUT = DATA / "entities.json"

CONFIRMED, UNCONFIRMED = "Confirmed", "Not confirmed"

# Companies looked at by hand, by Comptroller taxpayer number
REVIEWED = {
    "10106276719": {"group": "Project company", "link": CONFIRMED,
                    "note": "Named in Grimes County's agreements and holder of the site's state stormwater permit."},
    "32105982659": {"group": "Project company", "link": CONFIRMED,
                    "note": "Applicant on the school districts' tax agreements."},
    "32107538566": {"link": UNCONFIRMED,
                    "note": "Formed in August 2026 at a Plantersville mail service address used by many small businesses. "
                            "Nothing in the record ties it to SpaceX."},
}

# Looked at and found unrelated, so left out of the file. 32087666395 is a WIT TECH LLC formed in Brownsville in
# 2022; the WIT TECH LLC buying land in Grimes County gives a Palo Alto, California address in appraisal district
# records and is not in the Comptroller's data. A different WIT TECH appearing later would still be reported.
SKIP = {"32087666395"}

# What to search for: (group shown in the file, SoQL condition)
SEARCHES = [
    ("Name includes Terafab", "upper(taxpayer_name) like '%TERAFAB%' OR upper(taxpayer_name) like 'TERA FAB%' "
                              "OR upper(taxpayer_name) like '% TERA FAB%'"),
    ("Named WIT TECH", "upper(taxpayer_name) like 'WIT TECH%'"),
    ("Project company", "taxpayer_number = '10106276719'"),
    ("Same mailing address as SpaceX (1 Rocket Rd)", "upper(taxpayer_address) like '1 ROCKET R%'"),
    ("Same mailing address as the land buyer (1450 Page Mill Rd, Palo Alto)",
     "upper(taxpayer_address) like '1450 PAGE MILL%' AND upper(taxpayer_city) = 'PALO ALTO'"),
]
# A street address is kept only when it is one of these, the reason for the match. The fixed wording is
# written rather than the record's own line, which can carry an "Attn:" with a person's name.
WATCHED_STREETS = {"1 ROCKET R": "1 Rocket Rd", "1450 PAGE MILL": "1450 Page Mill Rd"}
# Companies found by name are followed closely: their agent and office are looked up and every change is
# logged. Companies found only by a shared address are kept as a baseline, so a new arrival stands out, but
# their later changes are not logged: most are Starlink and Starbase businesses with no tie to Grimes County.
NAME_GROUPS = {"Name includes Terafab", "Named WIT TECH", "Project company"}

# The Comptroller's two-letter organization codes. Others are shown as the code.
TYPES = {"CL": "Texas limited liability company", "CI": "Out-of-state limited liability company",
         "CT": "Texas corporation", "CF": "Out-of-state corporation",
         "CN": "Texas nonprofit corporation", "CM": "Out-of-state nonprofit corporation",
         "PL": "Texas limited partnership", "PF": "Out-of-state limited partnership"}
RIGHT_TO_TRANSACT = {"A": "Active"}
COMPANY_WORDS = re.compile(r"\b(INC|LLC|L\.L\.C|CORP|CORPORATION|COMPANY|CO|LP|LLP|LTD|PLLC|SERVICES?|AGENTS?|SYSTEMS?|GROUP)\b", re.I)
WATCHED = ("name", "type", "city", "state", "right_to_transact", "registration_status", "registered_agent", "registered_office")
LABELS = {"name": "name", "type": "organization type", "city": "city", "state": "state", "right_to_transact": "right to do business in Texas",
          "registration_status": "Secretary of State status", "registered_agent": "registered agent", "registered_office": "registered office"}


def text(value):
    return " ".join(str(value or "").split())


def day(value):
    return text(value)[:10] or None


def search(where):
    rows = fetch_json(DATASET, {"$where": where, "$limit": 1000, "$order": "taxpayer_number"})
    if len(rows) >= 1000:
        sys.exit(f"The Comptroller's data returned 1,000 rows for one search ({where}), its limit. Nothing written.")
    return rows


def registration(number):
    """Registered agent, office and status from the account status lookup. {} if it cannot be reached."""
    try:
        d = fetch_json(DETAIL.format(number), timeout=60).get("data") or {}
    except Exception as e:                      # the dataset is the main source; this is extra
        print(f"  Account status lookup failed for {number}: {e}")
        return {}
    agent = text(d.get("registeredAgentName"))
    found = {"registration_status": text(d.get("sosRegistrationStatus")).capitalize() or None,
             "state_of_formation": text(d.get("stateOfFormation")) or None}
    if agent and COMPANY_WORDS.search(agent):
        office = ", ".join(p for p in (text(d.get("registeredOfficeAddressStreet")), text(d.get("registeredOfficeAddressCity")),
                                       text(d.get("registeredOfficeAddressState")), text(d.get("registeredOfficeAddressZip"))) if p)
        found.update(registered_agent=agent, registered_office=office or None)
    elif agent:
        found.update(registered_agent="An individual (name not kept)")
    return found                                # officers' names in the response are never read


def entity(row, group, before):
    number = text(row["taxpayer_number"])
    street = text(row.get("taxpayer_address"))
    reviewed = REVIEWED.get(number, {})
    group = reviewed.get("group", group)
    link = reviewed.get("link", UNCONFIRMED)
    code = text(row.get("taxpayer_organizational_type"))
    right = text(row.get("right_to_transact_business_code"))
    e = {
        "taxpayer_number": number,
        "name": text(row["taxpayer_name"]),
        "group": group,
        "link_to_project": link,
        "note": reviewed.get("note"),
        "type": TYPES.get(code, f"Comptroller type code {code}" if code else None),
        "street": next((shown for start, shown in WATCHED_STREETS.items() if street.upper().startswith(start)), None),
        "city": text(row.get("taxpayer_city")).title() or None,
        "state": text(row.get("taxpayer_state")) or None,
        "registered_with_secretary_of_state": day(row.get("sos_charter_date")),
        "tax_responsibility_began": day(row.get("responsibility_beginning_date")),
        "secretary_of_state_file_number": text(row.get("secretary_of_state_sos_or_coa_file_number")) or None,
        "right_to_transact": RIGHT_TO_TRANSACT.get(right, f"Comptroller code {right}" if right else None),
    }
    if group in NAME_GROUPS:
        details = registration(number)
        # If the lookup is down, carry yesterday's values forward rather than report them as removed
        for key in ("registration_status", "state_of_formation", "registered_agent", "registered_office"):
            e[key] = details.get(key) if details else (before or {}).get(key)
    e["first_seen"] = (before or {}).get("first_seen", date.today().isoformat())
    e["url"] = ACCOUNT_PAGE.format(number)
    return {k: v for k, v in e.items() if v is not None}


def describe_new(e):
    began = e.get("registered_with_secretary_of_state") or e.get("tax_responsibility_began")
    where = ", ".join(p for p in (e.get("city"), e.get("state")) if p)
    return (f"Entity watch: new company on the state's franchise tax list, {e['name']}"
            f" ({e['group'][0].lower() + e['group'][1:]}; {e.get('type', 'type not recorded').lower()}"
            f"{', ' + where if where else ''}{', dated ' + began if began else ''}). Link to the project: {e['link_to_project'].lower()}.")


def main():
    old_file = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    before = {e["taxpayer_number"]: e for e in old_file["entities"]} if old_file else {}

    found = {}
    for group, where in SEARCHES:
        for row in search(where):
            number = text(row["taxpayer_number"])
            if number not in found and number not in SKIP:      # the first search to find a company names its group
                found[number] = entity(row, group, before.get(number))
    if not any(e["link_to_project"] == CONFIRMED for e in found.values()):
        sys.exit("The Comptroller's data returned none of the project's known companies, which looks like a bad response. Nothing written.")
    entities = sorted(found.values(), key=lambda e: (e["link_to_project"] != CONFIRMED, e["group"], e["name"]))

    changes = []
    if old_file is None:
        changes.append((f"Entity watch: new, tracking {len(entities)} companies in the Texas Comptroller's franchise tax list", DATASET_PAGE))
    else:
        for e in entities:
            was = before.get(e["taxpayer_number"])
            if not was:
                changes.append((describe_new(e), e["url"]))
                continue
            for key in WATCHED if e["group"] in NAME_GROUPS else ():
                if e.get(key) != was.get(key):
                    changes.append((f"Entity watch: {e['name']}: {LABELS[key]} changed from "
                                    f"{was.get(key) or 'not recorded'} to {e.get(key) or 'not recorded'}", e["url"]))
        for number, was in before.items():
            if number not in found and number not in SKIP and was["group"] in NAME_GROUPS:
                changes.append((f"Entity watch: {was['name']} is no longer on the Comptroller's list of active franchise taxpayers", was["url"]))

    OUT.write_text(json.dumps({
        "checked": date.today().isoformat(),
        "source": DATASET_PAGE,
        "about": "Companies in the Texas Comptroller's franchise tax records that are tied to the Terafab project, share its name, "
                 "or share a mailing address with SpaceX or with the company buying the land. Sharing an address or a name is not "
                 "proof of a link: see link_to_project. Only companies are listed; no officers or other people are named.",
        "not_in_the_data": "WIT TECH LLC, the company listed on the land in Grimes County appraisal records (mailing address "
                           "1450 Page Mill Rd, Palo Alto, California), is not in the Comptroller's franchise tax data. An "
                           "unrelated Texas company of the same name is left out of this list.",
        "how_closely": "Companies found by name are checked daily for changes to status, registered agent and registered office. "
                       "Companies found only by a shared mailing address are listed so that a new one stands out; their later "
                       "changes are not reported.",
        "entities": entities,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for summary, link in changes:
        log_change("entities", summary, link)
        print(f"  Change logged: {summary}")
    if not changes:
        print("  No change since the last run.")
    record_layer("entities", DATASET, len(entities))

    print(f"Wrote {len(entities)} companies to {OUT.name} ({OUT.stat().st_size / 1024:.1f} KB).")
    for e in entities:
        print(f"  {e['link_to_project']:<17} {e['name']:<40} {e['group']}")


if __name__ == "__main__":
    main()
