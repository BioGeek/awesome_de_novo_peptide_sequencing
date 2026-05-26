#!/usr/bin/env python3
"""Fetch GitHub activity metrics for every repository in algorithm_repository.

Run offline (not in CI). Re-execute when new repos are added or to refresh the
stars / issue / PR counts. Idempotent — UPSERTs into repository_metrics.

Uses the `gh` CLI for authentication so callers don't need to fiddle with
GITHUB_TOKEN. `gh auth login` must already be set up.

Per repository we record:
  - stars              from /repos
  - forks              from /repos
  - last_pushed        from /repos.pushed_at
  - open_issues        from /search/issues?q=...is:issue+is:open  (excludes PRs)
  - closed_issues      from /search/issues?q=...is:issue+is:closed
  - open_prs           from /search/issues?q=...is:pr+is:open
  - closed_prs         from /search/issues?q=...is:pr+is:closed

(`open_issues_count` on /repos is unusable in isolation — GitHub counts pull
requests as issues for that field, a longstanding quirk.)

Non-GitHub URLs (PyPI, anonymous.4open.science, project home pages, …) are
skipped — the script logs them and leaves the row absent.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "denovo.db"

GH_URL_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s#?]+)")
REPO_DELAY   = 0.5  # delay between /repos calls (5000/hr quota)
SEARCH_DELAY = 2.5  # delay between /search calls (30/min secondary limit — easy to trip)


def gh_api(path: str) -> dict | None:
    """Call `gh api <path>` and return the parsed JSON, or None on failure."""
    try:
        cp = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return json.loads(cp.stdout)
    except subprocess.CalledProcessError as e:
        print(f"  ! gh api {path}: {e.stderr.strip().splitlines()[0] if e.stderr else e}",
              file=sys.stderr)
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"  ! gh api {path}: {e}", file=sys.stderr)
        return None


def fetch_repo(owner: str, repo: str) -> dict | None:
    """Pull the metrics we need for one repo. Returns a dict or None on failure."""
    base = gh_api(f"/repos/{owner}/{repo}")
    if not base:
        return None
    time.sleep(REPO_DELAY)
    # Strip trailing .git if present in the URL.
    q_repo = f"{owner}/{repo}"
    counts = {}
    for kind, qual in [
        ("open_issues",   "is:issue+is:open"),
        ("closed_issues", "is:issue+is:closed"),
        ("open_prs",      "is:pr+is:open"),
        ("closed_prs",    "is:pr+is:closed"),
    ]:
        # The search-issues endpoint counts PRs as issues unless we use `is:issue`,
        # so the four buckets stay disjoint.
        path = f"/search/issues?q=repo:{q_repo}+{qual}&per_page=1"
        body = gh_api(path)
        counts[kind] = body.get("total_count", 0) if body else None
        time.sleep(SEARCH_DELAY)

    return {
        "stars":         base.get("stargazers_count"),
        "forks":         base.get("forks_count"),
        "last_pushed":   base.get("pushed_at"),
        **counts,
    }


def ensure_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS repository_metrics (
            url            TEXT PRIMARY KEY,
            stars          INTEGER,
            forks          INTEGER,
            open_issues    INTEGER,
            closed_issues  INTEGER,
            open_prs       INTEGER,
            closed_prs     INTEGER,
            last_pushed    TEXT,
            fetched_at     TEXT NOT NULL
        )
        """
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retry-missing", action="store_true",
                    help="Only fetch rows whose search-API counts are NULL "
                         "(handy after a partial run hits secondary rate-limits).")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur  = conn.cursor()
    ensure_table(cur)
    conn.commit()

    if args.retry_missing:
        urls = [u for (u,) in cur.execute(
            """SELECT url FROM repository_metrics
               WHERE open_issues IS NULL OR closed_issues IS NULL
                  OR open_prs   IS NULL OR closed_prs   IS NULL"""
        ).fetchall()]
        print(f"--retry-missing: {len(urls)} rows with NULL counts to refetch.")
    else:
        urls = [u for (u,) in cur.execute("SELECT DISTINCT url FROM algorithm_repository").fetchall()]
        print(f"Found {len(urls)} repo URLs.")
    gh_urls = [u for u in urls if GH_URL_RE.match(u)]
    skipped = [u for u in urls if not GH_URL_RE.match(u)]
    print(f"  → {len(gh_urls)} GitHub URLs, {len(skipped)} non-GitHub (skipped).")
    for u in skipped:
        print(f"  - skipped (not github): {u}")

    fetched_at = datetime.utcnow().isoformat(timespec="seconds")
    ok = fail = 0
    for idx, url in enumerate(gh_urls, 1):
        m = GH_URL_RE.match(url)
        owner, repo = m.group(1), m.group(2).removesuffix(".git")
        print(f"[{idx}/{len(gh_urls)}] {owner}/{repo}")
        metrics = fetch_repo(owner, repo)
        if not metrics:
            fail += 1
            continue
        cur.execute(
            """
            INSERT INTO repository_metrics
                (url, stars, forks, open_issues, closed_issues, open_prs, closed_prs,
                 last_pushed, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                stars         = excluded.stars,
                forks         = excluded.forks,
                open_issues   = excluded.open_issues,
                closed_issues = excluded.closed_issues,
                open_prs      = excluded.open_prs,
                closed_prs    = excluded.closed_prs,
                last_pushed   = excluded.last_pushed,
                fetched_at    = excluded.fetched_at
            """,
            (url, metrics["stars"], metrics["forks"],
             metrics["open_issues"], metrics["closed_issues"],
             metrics["open_prs"], metrics["closed_prs"],
             metrics["last_pushed"], fetched_at),
        )
        print(f"  ★ {metrics['stars']:>5}  "
              f"issues open/closed {metrics['open_issues']}/{metrics['closed_issues']}  "
              f"PRs open/closed {metrics['open_prs']}/{metrics['closed_prs']}  "
              f"last pushed {metrics['last_pushed']}")
        ok += 1

    conn.commit()
    conn.close()
    print(f"\nDone. ok={ok} failed={fail} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
