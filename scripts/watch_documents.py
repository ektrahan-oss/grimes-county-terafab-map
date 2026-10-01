"""Watch Grimes County for new or changed documents about SpaceX, Terafab or the reinvestment zone.

Two places are checked:

  County agreements page   The economic development page that lists the SpaceX agreements.
                           Every linked document whose name mentions a keyword is tracked.
  Commissioners Court      Agendas and minutes from the county's CivicClerk meeting portal,
                           read as plain text and searched for the keywords.

What has been seen is kept in data/document_hashes.json. New or changed items are logged in
data/changes.json with a link. The first run only records what is already there.

    python scripts/watch_documents.py
"""

import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser

from common import DATA, USER_AGENT, fetch, fetch_json, log_change, record_layer

KEYWORDS = ["SpaceX", "Terafab", "reinvestment zone"]

DOCS_PAGE = ("https://grimescountytexas.gov/index.asp"
             "?DE=41B01CCB-E7BC-4B13-AA5B-6E776B28053E&SEC=29DD23F1-21FF-472B-9DB3-CC5315345627")
DOC_TYPES = (".pdf", ".doc", ".docx", ".xls", ".xlsx")

AGENDA_API = "https://grimescotx.api.civicclerk.com/v1"
AGENDA_PORTAL = "https://grimescotx.portal.civicclerk.com/"
AGENDA_FILE_TYPES = {"Agenda", "Minutes"}      # not "Agenda Packet": those run to hundreds of pages
LOOK_BACK_DAYS = 45

STATE = DATA / "document_hashes.json"


def mentions(text):
    """Keywords found in text, ignoring case, spacing, underscores and hyphens."""
    flat = re.sub(r"[\s_\-]+", " ", text).lower()
    return [k for k in KEYWORDS if k.lower() in flat]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- County agreements page ----

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href.strip(), " ".join("".join(self._text).split())))
            self._href = None


def page_documents():
    """{url: title} for every keyword-matching document linked from the county page."""
    parser = Links()
    parser.feed(fetch(DOCS_PAGE).decode("utf-8", "replace"))
    found = {}
    for href, text in parser.links:
        url = urllib.parse.urljoin(DOCS_PAGE, html.unescape(href).strip())
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        if url.lower().split("?")[0].endswith(DOC_TYPES) and mentions(f"{text} {name}"):
            found[url] = text or name
    return found


def headers(url):
    """The server's own change markers, so unchanged files are not downloaded again."""
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/%?=&()"), method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return {"etag": r.headers.get("ETag"), "modified": r.headers.get("Last-Modified"),
                "bytes": int(r.headers.get("Content-Length") or 0)}


def check_page(seen, baseline):
    changes = []
    current = page_documents()
    for url, title in current.items():
        head, old = headers(url), seen.get(url)
        if old and all(old.get(k) == head[k] for k in head):
            continue
        digest = sha256(fetch(urllib.parse.quote(url, safe=":/%?=&()"), timeout=300))
        if old is None and not baseline:
            changes.append((f"County documents: new document posted, {title}", url))
        elif old and old["sha256"] != digest:
            changes.append((f"County documents: document changed, {title}", url))
        seen[url] = {"title": title, "sha256": digest, **head,
                     "first_seen": (old or {}).get("first_seen", date.today().isoformat())}
    for url in [u for u in seen if u not in current]:
        changes.append((f"County documents: no longer listed, {seen.pop(url)['title']}", DOCS_PAGE))
    return changes


# ---- Commissioners Court agendas ----

def agenda_files():
    """Agenda and minutes files for recent and upcoming meetings."""
    since = (date.today() - timedelta(days=LOOK_BACK_DAYS)).isoformat()
    query = urllib.parse.quote(f"$filter=startDateTime gt {since}T00:00:00Z&$orderby=startDateTime desc&$top=100",
                               safe="$=&")
    for event in fetch_json(f"{AGENDA_API}/Events?{query}")["value"]:
        for f in event.get("publishedFiles") or []:
            if f.get("type") in AGENDA_FILE_TYPES:
                yield {"id": str(f["fileId"]), "title": f["name"], "kind": f["type"],
                       "meeting": event["startDateTime"][:10],
                       "link": f"{AGENDA_API}/Meetings/GetMeetingFileStream(fileId={f['fileId']},plainText=false)"}


def check_agendas(seen, baseline):
    changes = []
    today, current = date.today().isoformat(), {}
    for f in agenda_files():
        old = seen.get(f["id"])
        current[f["id"]] = old
        # Files for past meetings are read once. Upcoming ones are re-read in case they are amended in place.
        if old and f["meeting"] < today:
            continue
        text = fetch(f"{AGENDA_API}/Meetings/GetMeetingFileStream(fileId={f['id']},plainText=true)").decode("utf-8", "replace")
        entry = {**f, "sha256": sha256(text.encode("utf-8")), "mentions": mentions(text)}
        del entry["id"]
        current[f["id"]] = entry
        if baseline or not entry["mentions"]:
            continue
        about = " and ".join(entry["mentions"])
        if old is None:
            changes.append((f"Commissioners Court: new {f['kind'].lower()} mentioning {about}, {f['title']}", f["link"]))
        elif old["sha256"] != entry["sha256"]:
            changes.append((f"Commissioners Court: {f['kind'].lower()} mentioning {about} changed, {f['title']}", f["link"]))
    seen.clear()                                # files older than the look-back window drop out
    seen.update(current)
    return changes


def main():
    baseline = not STATE.exists()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    documents, agendas = state.setdefault("documents", {}), state.setdefault("agendas", {})

    changes, failed = [], []
    for name, check, seen in [("county agreements page", check_page, documents),
                              ("Commissioners Court agendas", check_agendas, agendas)]:
        try:
            changes += check(seen, baseline)
        except Exception as e:                  # one source being down should not hide the other
            failed.append(f"{name}: {e}")

    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for summary, link in changes:
        log_change("documents", summary, link)
        print(f"  Change logged: {summary}")
    if not changes:
        print("  First run: recorded what is already posted." if baseline else "  No change since the last run.")

    matching = [a for a in agendas.values() if a["mentions"]]
    record_layer("documents", DOCS_PAGE, len(documents) + len(matching))
    print(f"Tracking {len(documents)} county documents and {len(agendas)} agenda or minutes files "
          f"from the last {LOOK_BACK_DAYS} days, {len(matching)} of which mention the project.")
    for a in sorted(matching, key=lambda a: a["meeting"], reverse=True):
        print(f"  {a['meeting']}  {a['title']}  ({', '.join(a['mentions'])})")
    if failed:
        sys.exit("Could not check: " + "; ".join(failed))


if __name__ == "__main__":
    main()
