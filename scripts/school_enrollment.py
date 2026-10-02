"""Enrollment by school year for Texas school districts, used by fetch_school_districts.py.

Source: Texas Education Agency, PEIMS Standard Reports, Student Enrollment. The agency's report
form offers every school year from 2011-12; the list of years is read from the form itself, so a
new year is picked up when the agency adds it (usually in the spring).

The statewide report by gender is used because its two rows per district add up to the district's
total. The reports by grade and by ethnicity hide small counts, so their rows cannot be added up.

    python scripts/school_enrollment.py        print the five districts' history
"""

import csv
import io
import re
import sys

from common import fetch

FORM = "https://rptsvr1.tea.texas.gov/adhocrpt/adste.html"
BROKER = "https://rptsvr1.tea.texas.gov/cgi/sas/broker"
QUERY = {"_service": "marykay", "_program": "adhoc.addispatch.sas", "major": "st", "minor": "e", "charsln": "120",
         "linespg": "60", "loop": "1", "countykey": "", "oldnew": "new", "_debug": "0",
         "selsumm": "sd",        # statewide, one total per district
         "key": "", "grouping": "s ", "format": "C"}
MIN_DISTRICTS = 900              # Texas has about 1,200; far fewer means a partial answer


def school_years():
    """[(code the form uses, '2025-26'), ...] oldest first."""
    page = fetch(FORM).decode("utf-8", "replace")
    menu = re.search(r'<select name="endyear".*?</select>', page, re.S)
    years = re.findall(r'value="(\d\d)">\s*(\d{4})-(\d{4})', menu.group(0)) if menu else []
    if not years:
        sys.exit("The Texas Education Agency's enrollment form no longer lists school years the way it did. Nothing written.")
    return sorted(((code, f"{start}-{end[2:]}") for code, start, end in years), key=lambda y: y[1])


def year_totals(code):
    """{district name: students} for one school year. A hidden count makes that district's total None."""
    text = fetch(BROKER, {**QUERY, "endyear": code}, timeout=300).decode("utf-8", "replace")
    start = next((i for i, line in enumerate(text.splitlines()) if line.upper().startswith('"YEAR"')), None)
    if start is None:
        sys.exit(f"The Texas Education Agency returned no enrollment table for year code {code}. Nothing written.")
    rows = list(csv.reader(io.StringIO("\n".join(text.splitlines()[start:]))))
    header = [h.upper() for h in rows[0]]
    name_at = header.index("DISTRICT NAME")              # the columns changed order over the years
    totals = {}
    for row in rows[1:]:
        if len(row) != len(header):
            continue
        name, count = row[name_at].strip().upper(), row[-1].strip()
        if name in totals and totals[name] is None:
            continue
        totals[name] = totals.get(name, 0) + int(count) if count.isdigit() else None
    if len(totals) < MIN_DISTRICTS:
        sys.exit(f"The Texas Education Agency returned only {len(totals)} districts for year code {code}, "
                 "which looks incomplete. Nothing written.")
    return totals


def enrollment(names):
    """{district name: {'2011-12': 2985, ...}} for the named districts (as the agency writes them, e.g. NAVASOTA ISD)."""
    history = {name: {} for name in names}
    for code, label in school_years():
        totals = year_totals(code)
        for name in names:
            if totals.get(name.upper()) is not None:
                history[name][label] = totals[name.upper()]
    missing = [name for name, years in history.items() if not years]
    if missing:
        sys.exit(f"The Texas Education Agency has no enrollment under the name {missing[0]}. Nothing written.")
    return history


def change(history, years_back):
    """'+5.1% (+152 students)' comparing the latest year with one this many years earlier, or None."""
    labels = sorted(history)
    if len(labels) <= years_back:
        return None
    now, then = history[labels[-1]], history[labels[-1 - years_back]]
    diff = now - then
    return f"{diff / then:+.1%} ({diff:+,} students)" if then else None


if __name__ == "__main__":
    for district, years in enrollment(["NAVASOTA ISD", "ANDERSON-SHIRO CISD", "IOLA ISD", "MADISONVILLE CISD", "RICHARDS ISD"]).items():
        print(district, years, change(years, 1), change(years, 5), sep="\n  ")
