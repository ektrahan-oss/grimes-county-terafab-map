"""Watch Grimes County for new or changed documents about SpaceX, Terafab or the reinvestment zone.

Two places are checked:

  County agreements page   The economic development page that lists the SpaceX agreements.
                           Every linked document whose name mentions a keyword is tracked.
  Commissioners Court      Agendas and minutes from the county's CivicClerk meeting portal,
                           read as plain text and searched for the keywords.

What has been seen is kept in data/document_hashes.json. New or changed items are logged in
data/changes.json with a link. The first run only records what is already there.

A copy of every file is kept for good in data/archive, in case the county later changes it or
takes it down. Nothing is ever deleted from there, and a changed file is kept beside the earlier one.

    python scripts/watch_documents.py
    python scripts/watch_documents.py --backfill     one time: also collect court files back to ARCHIVE_SINCE
"""

import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser

from common import DATA, USER_AGENT, fetch, fetch_json, log_change, record_layer

# The county often uses the company's formal name: its May 2026 agendas never said "SpaceX".
KEYWORDS = ["SpaceX", "Space Exploration Technologies", "Terafab", "Terrafab", "reinvestment zone"]   # the minutes sometimes spell it with two r's

DOCS_PAGE = ("https://grimescountytexas.gov/index.asp"
             "?DE=41B01CCB-E7BC-4B13-AA5B-6E776B28053E&SEC=29DD23F1-21FF-472B-9DB3-CC5315345627")
DOC_TYPES = (".pdf", ".doc", ".docx", ".xls", ".xlsx")

AGENDA_API = "https://grimescotx.api.civicclerk.com/v1"
AGENDA_PORTAL = "https://grimescotx.portal.civicclerk.com/"
AGENDA_FILE_TYPES = {"Agenda", "Minutes"}      # not "Agenda Packet": those run to hundreds of pages
LOOK_BACK_DAYS = 45
ARCHIVE_SINCE = "2026-01-01"                   # how far back --backfill reaches
MIN_TEXT = 200                                 # a file with fewer characters of text than this is treated as a scan
SCAN_DPI = 200

STATE = DATA / "document_hashes.json"
ARCHIVE = DATA / "archive"                     # copies kept for good


def mentions(text):
    """Keywords found in text, ignoring case, spacing, underscores and hyphens."""
    flat = re.sub(r"[\s_\-]+", " ", text).lower()
    return [k for k in KEYWORDS if k.lower() in flat]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def keep_copy(folder, name, data):
    """Save a copy in the archive, never overwriting one already kept. Returns its path within data/."""
    path = ARCHIVE / folder / re.sub(r'[<>:"/\\|?*\s]+', "_", name)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.relative_to(DATA).as_posix()


def read_scan(pdf):
    """Text of a scanned PDF, read page by page with Tesseract. Empty if the tools are not installed."""
    tesseract = shutil.which("tesseract") or shutil.which("tesseract", path=r"C:\Program Files\Tesseract-OCR")
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if not tesseract or not pymupdf:
        print("  A scanned file could not be read: Tesseract and the pymupdf package are needed.")
        return ""
    pages = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            image = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY).tobytes("png")
            done = subprocess.run([tesseract, "stdin", "stdout", "-l", "eng"], input=image, capture_output=True, timeout=300)
            pages.append(done.stdout.decode("utf-8", "replace").strip())
    return "\n\n".join(pages)


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
    today = date.today().isoformat()
    for url, title in current.items():
        head, old = headers(url), seen.get(url)
        if old and old.get("copy") and "removed" not in old and all(old.get(k) == head[k] for k in head):
            continue
        body = fetch(urllib.parse.quote(url, safe=":/%?=&()"), timeout=300)
        digest = sha256(body)
        earlier = list((old or {}).get("earlier", []))
        if old is None and not baseline:
            changes.append((f"County documents: new document posted, {title}", url))
        elif old and old["sha256"] != digest:
            changes.append((f"County documents: document changed, {title}", url))
            earlier.append({"sha256": old["sha256"], "copy": old.get("copy"), "replaced": today})
        elif old and "removed" in old:
            changes.append((f"County documents: listed again, {title}", url))
        name = urllib.parse.unquote(url.split("?")[0].rsplit("/", 1)[-1])
        seen[url] = {"title": title, "sha256": digest, **head, "first_seen": (old or {}).get("first_seen", today),
                     "copy": keep_copy("county", f"{digest[:8]}_{name}", body)}
        if earlier:
            seen[url]["earlier"] = earlier
    for url, old in seen.items():               # delisted documents stay on file, with the date they went
        if url not in current and "removed" not in old:
            old["removed"] = today
            changes.append((f"County documents: no longer listed, {old['title']}"
                            + (" (a copy is kept)" if old.get("copy") else ""), DOCS_PAGE))
    return changes


# ---- Commissioners Court agendas ----

def agenda_files(since):
    """Agenda and minutes files for meetings after a date, upcoming ones included."""
    query = urllib.parse.quote(f"$filter=startDateTime gt {since}T00:00:00Z&$orderby=startDateTime desc", safe="$=&")
    url = f"{AGENDA_API}/Events?{query}"
    while url:                                  # the portal hands out 15 meetings at a time
        page = fetch_json(url)
        for event in page["value"]:
            for f in event.get("publishedFiles") or []:
                if f.get("type") in AGENDA_FILE_TYPES:
                    yield {"id": str(f["fileId"]), "title": f["name"], "kind": f["type"],
                           "meeting": event["startDateTime"][:10],
                           "link": f"{AGENDA_API}/Meetings/GetMeetingFileStream(fileId={f['fileId']},plainText=false)"}
        url = page.get("@odata.nextLink")


def check_agendas(seen, baseline, backfill=False):
    changes, listed = [], set()
    today = date.today().isoformat()
    since = ARCHIVE_SINCE if backfill else (date.today() - timedelta(days=LOOK_BACK_DAYS)).isoformat()
    for f in agenda_files(since):
        old = seen.get(f["id"])
        listed.add(f["id"])
        # Files for past meetings are read once. Upcoming ones are re-read in case they are amended in place.
        if old and old.get("copy") and "removed" not in old and f["meeting"] < today:
            continue
        text = fetch(f"{AGENDA_API}/Meetings/GetMeetingFileStream(fileId={f['id']},plainText=true)").decode("utf-8", "replace")
        pdf = fetch(f["link"], timeout=300)
        digest, scanned = sha256(text.encode("utf-8")), len(text.strip()) < MIN_TEXT
        if scanned:                             # a scanned image: fingerprint the file itself and read it by eye
            digest = sha256(pdf)
            if old and old["sha256"] == digest and old.get("copy"):
                continue                        # the same scan as last time, already read
            text = read_scan(pdf)
        name = f"{f['meeting']}_{f['kind'].lower()}_{f['id']}_{digest[:8]}"
        entry = {**f, "sha256": digest, "mentions": mentions(text),
                 "first_seen": (old or {}).get("first_seen", today),
                 "copy": keep_copy("court", name + ".pdf", pdf)}
        if scanned:
            entry["text"] = "read from scan" if text.strip() else "scan not read"
        keep_copy("court", name + ".txt", text.encode("utf-8"))
        del entry["id"]
        earlier = list((old or {}).get("earlier", []))
        if old and old["sha256"] != digest:
            earlier.append({"sha256": old["sha256"], "copy": old.get("copy"), "replaced": today})
        if earlier:
            entry["earlier"] = earlier
        seen[f["id"]] = entry
        about = " and ".join(entry["mentions"])
        if old and "removed" in old:
            changes.append((f"Commissioners Court: {f['kind'].lower()} listed again, {f['title']}", f["link"]))
        elif baseline or (backfill and old is None):
            continue                            # collecting what is already posted is not news
        elif old and old["sha256"] != digest:
            if about or f["meeting"] < today:   # a past meeting's file changing is worth a note whatever it says
                changes.append((f"Commissioners Court: {f['kind'].lower()} changed after posting, {f['title']}"
                                + (f" (mentions {about})" if about else ""), f["link"]))
        elif old is None and about:
            changes.append((f"Commissioners Court: new {f['kind'].lower()} mentioning {about}, {f['title']}", f["link"]))
    # Files older than the look-back window are simply no longer asked for. One inside it that has gone is worth a note.
    for file_id, old in seen.items():
        if file_id not in listed and old["meeting"] >= since and "removed" not in old:
            old["removed"] = today
            changes.append((f"Commissioners Court: {old['kind'].lower()} no longer listed, {old['title']}"
                            + (" (a copy is kept)" if old.get("copy") else ""), AGENDA_PORTAL))
    return changes


def main():
    backfill = "--backfill" in sys.argv[1:]
    baseline = not STATE.exists()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    documents, agendas = state.setdefault("documents", {}), state.setdefault("agendas", {})

    changes, failed = [], []
    for name, check, seen in [("county agreements page", check_page, documents),
                              ("Commissioners Court agendas", lambda s, b: check_agendas(s, b, backfill), agendas)]:
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
    listed = [d for d in documents.values() if "removed" not in d]
    record_layer("documents", DOCS_PAGE, len(listed) + len(matching), complete=not failed)
    kept = [f for f in ARCHIVE.rglob("*") if f.is_file() and ".git" not in f.parts] if ARCHIVE.exists() else []
    print(f"Tracking {len(listed)} county documents and {len(agendas)} agenda or minutes files, "
          f"{len(matching)} of which mention the project.")
    print(f"Archive: {len(kept)} files kept, {sum(f.stat().st_size for f in kept) / 1e6:.1f} MB.")
    for a in sorted(matching, key=lambda a: a["meeting"], reverse=True):
        print(f"  {a['meeting']}  {a['title']}  ({', '.join(a['mentions'])})")
    if failed:
        sys.exit("Could not check: " + "; ".join(failed))


if __name__ == "__main__":
    main()
