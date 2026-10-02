"""Build a short news feed about the Terafab project into data/news.json.

Source: Google News RSS search results for the Terafab project in Grimes County. Only the headline, publisher, publish
date and link are kept. No article text or summaries are stored.

    python scripts/build_news.py
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

from common import DATA, fetch, record_layer

FEED = "https://news.google.com/rss/search"
# Each search needs a local term as well as the project, because Google matches the article
# text: "Terafab" alone pulls in national Tesla and SpaceX stock coverage.
SEARCHES = ['Terafab "Grimes County"', 'SpaceX "Grimes County"', '"Gibbons Creek" (Terafab OR SpaceX)']
KEEP = 50
SIMILAR = 0.85                   # titles at least this alike count as the same story
PAUSE_SECONDS = 1
OUT = DATA / "news.json"


def search(query):
    raw = fetch(FEED, {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    items = []
    for item in ET.fromstring(raw).iter("item"):
        source = (item.findtext("source") or "").strip()
        title = (item.findtext("title") or "").strip()
        if source and title.endswith(f" - {source}"):       # Google appends the publisher to the headline
            title = title[: -len(source) - 3].strip()
        link, published = item.findtext("link"), item.findtext("pubDate")
        if not (title and link and published):
            continue
        date = parsedate_to_datetime(published).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items.append({"title": title, "source": source, "date": date, "link": link.strip()})
    return items


def key(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def dedupe(items):
    """Keep the earliest copy of each story: same link, or near-identical headline."""
    kept, links = [], set()
    for item in sorted(items, key=lambda i: i["date"]):
        k = key(item["title"])
        if item["link"] in links or any(SequenceMatcher(None, k, key(o["title"])).ratio() >= SIMILAR for o in kept):
            continue
        links.add(item["link"])
        kept.append(item)
    return kept


def main():
    found = []
    for i, query in enumerate(SEARCHES):
        if i:
            time.sleep(PAUSE_SECONDS)
        results = search(query)
        print(f"  {len(results):>3} results for {query!r}")
        found += results
    if not found:
        sys.exit("Google News returned nothing for any search. Nothing written.")

    # Google returns a slightly different set each time, and names a publisher sometimes by name and
    # sometimes by web address. Start from the stories already on file and keep their entries, so the
    # list only changes when something newer arrives.
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    known = {n["link"]: n for n in old}
    unique = dedupe(old + [n for n in found if n["link"] not in known])
    news = sorted(unique, key=lambda i: i["date"], reverse=True)[:KEEP]
    OUT.write_text("[\n" + ",\n".join("  " + json.dumps(n, ensure_ascii=False) for n in news) + "\n]\n", encoding="utf-8")
    # New stories are not written to data/changes.json: the News tab already shows them, and
    # daily entries would crowd out layer and document changes.
    print(f"  {len({n['link'] for n in news} - set(known))} new since the last run.")
    record_layer("news", FEED, len(news))
    print(f"{len(found)} results, {len(unique)} after removing duplicates; wrote the newest {len(news)} to {OUT.name} "
          f"({OUT.stat().st_size / 1024:.0f} KB), dated {news[-1]['date'][:10]} to {news[0]['date'][:10]}.")


if __name__ == "__main__":
    main()
