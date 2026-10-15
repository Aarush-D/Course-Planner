"""Scrape each degree-plan file's bulletin page for the "At which campus can
I study this program?" picker (the `#campus-list` block: a "Begin Campus"
and an "End Campus" list) and cache the parsed result as JSON.

Read-only against bulletins.psu.edu; writes only the cache file named on
the command line. Applying results to degree_plans/*.json is a separate,
deliberate step (apply_campus_availability.py) so the raw scrape can be
inspected and re-used without re-fetching ~750 pages.

    python scripts/scrape_campus_availability.py CACHE.json [--limit N]

Resumable: URLs already in CACHE.json are skipped.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bs4 import BeautifulSoup  # noqa: E402
from campus_availability import parse_campus_availability  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELAY_S = 0.15
USER_AGENT = "CourseyCampusAudit/1.0 (student project; contact via repo)"


def source_urls() -> Dict[str, List[str]]:
    urls: Dict[str, List[str]] = {}
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "degree_plans", "*.json"))):
        with open(path) as f:
            d = json.load(f)
        s = d.get("source")
        if isinstance(s, dict):
            s = s.get("url")
        if isinstance(s, str) and "bulletins.psu.edu" in s:
            urls.setdefault(s, []).append(os.path.basename(path))
    return urls


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cache_path = argv[0]
    limit: Optional[int] = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    cache: Dict[str, Any] = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
    # A cached fetch error is retried, not trusted: it says nothing about the page.
    todo = [u for u in source_urls() if u not in cache or "error" in cache[u]]
    if limit:
        todo = todo[:limit]
    print(f"{len(cache)} cached, {len(todo)} to fetch")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for i, url in enumerate(todo, 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code != 200:
                cache[url] = {"error": f"http {r.status_code}"}
            else:
                parsed = parse_campus_availability(r.text)
                frag = BeautifulSoup(r.text, "html.parser").find(id="campus-study")
                parsed["block_html"] = str(frag) if frag is not None else ""
                cache[url] = parsed
        except Exception as e:  # network blip: record and keep going
            cache[url] = {"error": f"{type(e).__name__}: {e}"}
        if i % 25 == 0:
            with open(cache_path, "w") as f:
                json.dump(cache, f)
            print(f"  {i}/{len(todo)}")
        time.sleep(DELAY_S)
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
