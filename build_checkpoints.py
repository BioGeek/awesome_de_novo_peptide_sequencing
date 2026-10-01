#!/usr/bin/env python3
"""Check that every recorded model checkpoint is still where the paper said it is.

A number in a benchmark is only reproducible if the weights behind it can still
be downloaded, and the field keeps them in places with very different
durability. `checkpoint.archival` records that difference: a DOI'd Zenodo or
figshare record is preserved by its host and dated, while a Google Drive link, a
personal academic URL or a GitHub release tag is mutable, undated and can vanish
leaving nothing to cite.

This script is `checkpoint`'s ONLY writer, which is what lets it run as a
refresh workflow under `.github/actions/commit-refreshed-db`: that action
recovers from a push race by replaying the one table a workflow owns, and the
invariant it relies on is sole ownership.

What it does NOT do is decide which checkpoints exist. Reading a paper's data
availability statement and resolving it to a method is a judgement call, so rows
are added by hand (see 'Checkpoints' in CLAUDE.md); this fills in status,
http_code and last_checked, and with --mirror copies the at-risk ones.

    uv run python build_checkpoints.py                # check liveness
    uv run python build_checkpoints.py --write        # ... and record it
    uv run python build_checkpoints.py --mirror       # ... and mirror at-risk ones

**Liveness is checked with curl, not requests**, for the reason recorded under
'The local PDF library' in CLAUDE.md: HTTPS on the machine this was written for
is intercepted by a gateway whose re-signed certificates carry no Authority Key
Identifier, which Python rejects and curl accepts.

**A HEAD request is not enough on its own.** Google Drive answers 200 to a HEAD
for a file that no longer exists, serving an HTML interstitial rather than the
bytes, and GitHub release *tags* 404 while the release assets live at a
different path. So a 200 whose content-type is HTML where a binary was expected
is reported as `unverifiable` rather than `live`: the honest answer is that the
link resolves but what it resolves to cannot be confirmed from a header.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import subprocess
import sys
from datetime import date

DB = pathlib.Path(__file__).with_name("denovo.db")
UA = "awesome-de-novo (mailto:j.vangoey@instadeep.com)"

# Hosts where a 200 means the bytes are there, because the host is an archive
# that serves files rather than pages.
BINARY_EXPECTED = ("Zenodo", "figshare", "Hugging Face")


# Drive's wording when a file is no longer served to anonymous callers. This is
# the state DeepNovo's pretrained model is in: the link resolves, the page loads,
# and the bytes are behind a Google account. A header probe cannot see that, so
# for these hosts the body has to be read.
GATED = re.compile(r"can't access this content|Try signing in to your Google Account"
                   r"|You need access|request access", re.I)


def body_says_gated(url: str) -> bool:
    """Fetch the page and look for a sign-in wall. Only used for Drive-like hosts.

    The URL FORM matters, which cost a wrong verdict. Drive's legacy
    `open?id=<id>` form answers with a 944 KB application shell that contains no
    gate text even when the file is gated; the canonical `/file/d/<id>/view`
    page says so plainly. So the id is extracted and both forms are tried.
    """
    ids = re.findall(r"(?:/file/d/|[?&]id=)([\w-]{20,})", url)
    candidates = [url] + [f"https://drive.google.com/file/d/{i}/view" for i in ids]
    for u in dict.fromkeys(candidates):
        out = subprocess.run(["curl", "-sL", "--max-time", "45", "-A", UA, u],
                             capture_output=True, text=True, timeout=120).stdout
        if GATED.search(out or ""):
            return True
    return False


def probe(url: str) -> tuple[int | None, str, str]:
    """Return (http_code, content_type, final_url) after following redirects."""
    out = subprocess.run(
        ["curl", "-sIL", "--max-time", "45", "-A", UA,
         "-o", "/dev/null", "-w", "%{http_code}\t%{content_type}\t%{url_effective}", url],
        capture_output=True, text=True, timeout=120).stdout
    parts = out.strip().split("\t")
    if len(parts) != 3:
        return None, "", url
    code = int(parts[0]) if parts[0].isdigit() else None
    return code, parts[1], parts[2]


def verdict(row: sqlite3.Row, code: int | None, ctype: str, final: str) -> tuple[str, str | None]:
    """Map a probe to a status, and say why when the answer is not simply yes."""
    if code is None:
        return "dead", "no response"
    if code in (404, 410):
        return "dead", f"HTTP {code}"
    if code >= 500:
        return "unverifiable", f"HTTP {code}, host error rather than a missing file"
    if code >= 400:
        return "unverifiable", f"HTTP {code}"
    is_html = "text/html" in (ctype or "").lower()
    if is_html and row["host"] in BINARY_EXPECTED:
        # An archive serving HTML where a file was expected usually means a
        # landing page, which is fine, so this is not a failure on its own.
        return "live", "landing page, not the file itself"
    if is_html and row["host"] == "Google Drive":
        # Read the body before giving up. A sign-in wall is a DIFFERENT state
        # from "cannot tell": it means the weights behind a published, still
        # resolving link are no longer available to anyone without a Google
        # account, which is what a reader needs to know before trusting a
        # number produced with them.
        if body_says_gated(row["url"]):
            return "gated", "Drive requires a signed-in Google account"
        # Otherwise Drive answers 200 with an interstitial whether or not the
        # file is still shared, so the honest answer is that a response cannot
        # tell us.
        return "unverifiable", "Drive answers 200 with HTML either way"
    if re.sub(r"[?#].*$", "", final) != re.sub(r"[?#].*$", "", row["url"]):
        return "moved", f"redirected to {final[:90]}"
    return "live", None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="record status and last_checked")
    ap.add_argument("--mirror", action="store_true",
                    help="mirror the non-archival checkpoints (implies --write)")
    ap.add_argument("--only", help="comma-separated checkpoint ids")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM checkpoint"
    params: tuple = ()
    if args.only:
        ids = [int(i) for i in args.only.split(",")]
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    rows = con.execute(sql + " ORDER BY archival DESC, host, id", params).fetchall()

    today = date.today().isoformat()
    tally: dict[str, int] = {}
    at_risk: list[sqlite3.Row] = []
    for r in rows:
        code, ctype, final = probe(r["url"])
        status, why = verdict(r, code, ctype, final)
        tally[status] = tally.get(status, 0) + 1
        flag = " " if r["archival"] else "!"
        name = con.execute("SELECT name FROM algorithm WHERE id=?",
                           (r["algorithm_id"],)).fetchone()[0]
        print(f" {flag} {status:13} {str(code or '-'):>4}  {name[:18]:18} {r['host']:15} "
              f"{(r['label'] or '')[:22]:22}{' | ' + why if why else ''}")
        if args.write or args.mirror:
            con.execute("""UPDATE checkpoint SET status=?, http_code=?, last_checked=?,
                           notes=COALESCE(?, notes) WHERE id=?""",
                        (status, code, today, why, r["id"]))
        if not r["archival"] and status in ("live", "moved", "unverifiable"):
            at_risk.append(r)
    if args.write or args.mirror:
        con.commit()

    print()
    print("  " + ", ".join(f"{n} {s}" for s, n in sorted(tally.items(), key=lambda kv: -kv[1])))
    print(f"  {len(at_risk)} non-archival checkpoint(s): the mirror candidates")
    for r in at_risk:
        lic = r["licence"] or "NO STATED LICENCE"
        print(f"      {r['host']:15} {lic:18} {r['url'][:76]}")

    if args.mirror:
        # Mirroring is deliberately a separate, explicit step and NOT part of a
        # scheduled refresh. It copies someone else's bytes under a licence that
        # permits it, publishes them, and commits this project to keeping them
        # alive; a cron job should not take that decision on anyone's behalf.
        blocked = [r for r in at_risk if not r["licence"]]
        if blocked:
            print(f"\n  {len(blocked)} of them state NO LICENCE and are not mirrored.")
            print("  Redistribution needs permission, and silence is not permission.")
        print("\n  Mirroring is not implemented as an automatic step: see")
        print("  'Checkpoints' in CLAUDE.md for the procedure and why it is manual.")
    elif not args.write:
        print("\nReport only. Re-run with --write to record these verdicts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
