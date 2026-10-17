#!/usr/bin/env python3
"""Tell Bing (and every other IndexNow engine) which pages a publish changed.

IndexNow is a push protocol: instead of waiting for a crawler to rediscover a
page, the site POSTs the changed URLs to api.indexnow.org, which shares them
with Bing, Yandex, Seznam, Naver and the rest. Google does not take part and
reads the sitemap as before. Bing's index is also what Copilot answers from.

    python3 scripts/indexnow.py write-key        # put the key file in _site
    python3 scripts/indexnow.py submit           # POST .indexnow-urls

THE KEY IS PUBLIC BY DESIGN. Ownership is proved by serving a file named
`<key>.txt` whose content is the key, so the key cannot be a secret: anyone can
read it off the site. What it proves is that whoever submits controls the
site. The file lives under this project's path rather than at the domain root,
which IndexNow allows through `keyLocation`, and which limits the key to URLs
under that path -- exactly the pages this site owns.

WHICH URLS. render_scope.py writes `.indexnow-urls`: every generated page whose
.qmd differs from the last publish's manifest, or is new, plus the home page
when any of them did. NOT every page a full render rewrites: a Quarto upgrade or
a _quarto.yml change re-renders all 7500 pages without changing what any of
them says, and submitting 7500 unchanged URLs is what IndexNow asks sites not
to do. The daily repo-metrics commit changes no page, so it submits nothing.

NEVER FAILS THE PUBLISH. The site is live by the time this runs; a refused or
unreachable ping costs some crawl latency and nothing else, so every failure is
a GitHub warning and exit 0. It does wait, up to five minutes, for the key file
to be served: gh-pages deploys a minute or so after the push, and a submission
whose key cannot be fetched is answered 403.

Stdlib only, like the other publish-step scripts.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
URLS = ROOT / ".indexnow-urls"
KEY = "d3dad3031092dd3ba8b29901bfa12360"
ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10_000                     # the protocol's per-request maximum


def site_url() -> str:
    from postrender import site_url as read  # one reader of _quarto.yml
    return read()


def write_key() -> int:
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / f"{KEY}.txt").write_text(KEY, encoding="utf-8")
    return 0


def warn(msg: str) -> int:
    print(f"::warning::indexnow: {msg}")
    return 0


def key_is_live(url: str, wait: float = 300, every: float = 15) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            with urllib.request.urlopen(f"{url}?t={int(time.time())}", timeout=20) as r:
                if r.read().decode().strip() == KEY:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            return False
        time.sleep(every)


def submit() -> int:
    if not URLS.exists():
        print("indexnow: no .indexnow-urls, nothing to submit")
        return 0
    urls = [u for u in URLS.read_text(encoding="utf-8").split("\n") if u.strip()]
    if not urls:
        print("indexnow: no page changed, nothing to submit")
        return 0
    base = site_url()
    key_url = f"{base}{KEY}.txt"
    if not key_is_live(key_url):
        return warn(f"key file not served at {key_url} after 5 minutes; skipped "
                    f"{len(urls)} URLs")
    for i in range(0, len(urls), BATCH):
        body = json.dumps({"host": urlsplit(base).hostname, "key": KEY,
                           "keyLocation": key_url,
                           "urlList": urls[i:i + BATCH]}).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (urllib.error.URLError, OSError) as e:
            return warn(f"POST failed: {e}")
        # 200 accepted; 202 accepted, key validation pending. Anything else
        # (400 bad request, 403 key not served, 422 URL outside the key's
        # path, 429 too many requests) is the engine refusing the batch.
        if code not in (200, 202):
            return warn(f"HTTP {code} for {len(urls[i:i + BATCH])} URLs")
        print(f"indexnow: HTTP {code}, submitted {len(urls[i:i + BATCH])} URLs")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "write-key":
        return write_key()
    if cmd == "submit":
        return submit()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
