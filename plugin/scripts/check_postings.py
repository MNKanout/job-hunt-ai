#!/usr/bin/env python3
"""scripts/check_postings.py — is each job ad still open?

    python3 scripts/check_postings.py <url> [<url> ...]
    python3 scripts/check_postings.py --snapshot <file> --url <url>

Rolling ads ("Snarest") have no deadline, so the pipeline cannot tell from its
own data that one has closed. This fetches each ad and looks for the board's
own words for a closed ad. It reads only; /check-expired shows the result and
the user decides what to mark expired.

Each URL gets a state:
  closed   the board says so (a closed-ad phrase, or the page is gone: 404/410)
  open     the page loaded and says nothing of the kind
  unclear  it could not tell: the request failed or was refused, a login wall,
           or a LinkedIn page with no Apply button (LinkedIn only says "No
           longer accepting applications" to a logged-in user)

--snapshot classifies a page the plugin's browser saved with browser_snapshot
instead, for the ads a plain request could not settle. There is no status code
then, so a page with no closed phrase is open.

Prints one JSON line: {"status": "ok", "results": [{url, state, evidence}]}.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

USER_AGENT = "job-hunt-ai-plugin (+https://github.com/MNKanout/job-hunt-ai)"
TIMEOUT = 20

# The words a board uses on an ad that has closed. Matched case-insensitively.
CLOSED = (
    "fristen er utløpt",                   # FINN
    "søknadsfristen er utløpt",
    "annonsen er utløpt",
    "no longer accepting applications",    # LinkedIn
    "this job is no longer available",
    "this job has expired",
    "job has expired",
)
LOGIN = ("authwall", "sign in to view", "log in to view", "logg inn for å se")
# LinkedIn's logged-out top card: an open job has an Apply button in it.
LINKEDIN_CTA = "top-card-layout__cta-container"


def classify(text: str) -> tuple[str, str]:
    """A page's state from its text alone."""
    low = text.lower()
    for phrase in CLOSED:
        if phrase in low:
            return "closed", f'says "{phrase}"'
    for phrase in LOGIN:
        if phrase in low:
            return "unclear", "a login wall"
    at = low.find(LINKEDIN_CTA)
    if at >= 0 and ">apply" not in low[at:at + 3000].replace(" ", "").replace("\n", ""):
        return "unclear", "no Apply button (LinkedIn may have closed it)"
    return "open", "no closed-ad notice"


def check(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,nb;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read(3_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return {"url": url, "state": "closed", "evidence": f"the page is gone (HTTP {e.code})"}
        return {"url": url, "state": "unclear", "evidence": f"the board refused the request (HTTP {e.code})"}
    except Exception as e:  # timeouts, refused connections, TLS errors
        return {"url": url, "state": "unclear", "evidence": f"could not load the page ({type(e).__name__})"}
    state, evidence = classify(text)
    return {"url": url, "state": state, "evidence": evidence}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="*", help="job ad URLs to fetch and check")
    parser.add_argument("--snapshot", help="a browser_snapshot file to check instead of fetching")
    parser.add_argument("--url", help="the ad the snapshot is of (with --snapshot)")
    a = parser.parse_args(argv)

    if a.snapshot:
        if not a.url or a.urls:
            parser.error("--snapshot takes exactly one --url and no other URLs")
        try:
            text = Path(a.snapshot).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(json.dumps({"status": "failed", "error": "snapshot_unreadable", "message": str(e)}))
            return 1
        state, evidence = classify(text)
        if state == "unclear" and "Apply button" in evidence:
            state, evidence = "open", "no closed-ad notice"
        print(json.dumps({"status": "ok", "results": [{"url": a.url, "state": state, "evidence": evidence}]}))
        return 0

    if not a.urls:
        parser.error("give at least one URL")
    bad = [u for u in a.urls if not u.startswith(("http://", "https://"))]
    if bad:
        print(json.dumps({"status": "failed", "error": "bad_url", "message": f"not a web address: {bad[0]}"}))
        return 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, a.urls))
    print(json.dumps({"status": "ok", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
