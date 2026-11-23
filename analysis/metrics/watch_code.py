"""Report evaluation code that changed upstream since implementations.json recorded it.

    GITHUB_TOKEN=... python3 analysis/metrics/watch_code.py [--out report.md]

Each row of implementations.json names the file and commit its behaviour was read at. For the
CURRENT row of every tool (the last one listed for it: earlier rows describe closed version
ranges, which cannot change), this asks GitHub for that file's blob at the recorded commit and
on the default branch now. A different blob means the code that computes the page's numbers
may have changed, and someone should re-read it: a new row if the behaviour changed, or just a
new `ref` if it did not. Repositories and files that have disappeared are reported too.

Exit status: 0 when nothing changed, 3 when something did (the weekly workflow turns that into
one GitHub issue, kept up to date). Stdlib only. The token is optional but the unauthenticated
API allows 60 requests an hour, about one run.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.github.com"


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "awesome-de-novo-metrics-watch"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def blob(repo, path, ref=None):
    """The git blob sha of a file, or None if it does not exist there."""
    q = f"?ref={urllib.parse.quote(ref)}" if ref else ""
    d = get(f"{API}/repos/{repo}/contents/{urllib.parse.quote(path)}{q}")
    return d.get("sha") if isinstance(d, dict) else None


def current_rows():
    latest = {}
    for c in json.loads((HERE / "implementations.json").read_text(encoding="utf-8")):
        latest[c["tool"]] = c  # rows are in version order: the last one per tool is current
    return list(latest.values())


def main():
    out_path = None
    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
    changed, gone, same = [], [], 0
    for c in current_rows():
        repo, path, ref = c["repo"], c["path"], c["ref"]
        if get(f"{API}/repos/{repo}") is None:
            gone.append(f"- **{c['tool']}**: the repository `{repo}` is gone or private.")
            continue
        # 'watch' names the branch to compare against when the default branch does not hold the
        # code: π-HelixNovo's main has only a README, its code is on the pi-HelixNovoV2 branch.
        branch = c.get("watch")
        then, now = blob(repo, path, ref), blob(repo, path, branch)
        if then is None:
            gone.append(f"- **{c['tool']}**: `{path}` is not at the recorded commit `{ref}`; "
                        "the row in implementations.json is wrong.")
        elif now is None:
            gone.append(f"- **{c['tool']}**: `{path}` is no longer on {f'branch `{branch}`' if branch else 'the default branch'} of `{repo}` "
                        f"(moved or deleted since `{ref}`).")
        elif then != now:
            sha_q = f"&sha={urllib.parse.quote(branch)}" if branch else ""
            commits = get(f"{API}/repos/{repo}/commits?path={urllib.parse.quote(path)}&per_page=1{sha_q}") or []
            head = commits[0]["sha"][:10] if commits else "HEAD"
            when = commits[0]["commit"]["committer"]["date"][:10] if commits else "?"
            changed.append(f"- **{c['tool']}** ({c['versions']}): [`{path}`](https://github.com/{repo}/blob/{head}/{path}) "
                           f"changed, last on {when}. [Compare `{ref}`...`{head}`]"
                           f"(https://github.com/{repo}/compare/{ref}...{head})")
        else:
            same += 1

    lines = ["The evaluation code behind these rows of `analysis/metrics/implementations.json` is no "
             "longer what the metrics page describes. For each one, read the diff: if the behaviour "
             "changed, add a row for the new version range; if not, move the row's `ref` to the new "
             "commit. Then run `analysis/metrics/run_all.sh`. This issue is updated by the weekly "
             "`watch-metric-code` workflow and closed when nothing is left.", ""]
    if changed:
        lines += ["### Changed", "", *changed, ""]
    if gone:
        lines += ["### Gone", "", *gone, ""]
    lines += [f"{same} other tracked file(s) are unchanged."]
    report = "\n".join(lines) + "\n"
    if out_path:
        out_path.write_text(report, encoding="utf-8")
    print(report)
    return 3 if (changed or gone) else 0


if __name__ == "__main__":
    sys.exit(main())
