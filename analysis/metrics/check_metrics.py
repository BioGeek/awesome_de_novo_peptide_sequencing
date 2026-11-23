"""Keep the metrics page in step with the catalog.

    python3 analysis/metrics/check_metrics.py          # report, exit 1 if anything is out of step
    python3 analysis/metrics/check_metrics.py --fix    # add rows for new papers, rebuild the data

Three things can go stale without anyone touching this folder:

1. A deep-learning method paper is added to denovo.db and has no row in papers.jsonl, so the
   page silently leaves it out. --fix appends a row marked "not yet read", which the page
   counts among the papers it has not read and lists in that number's appendix entry. Filling
   the row in needs the paper's full text: see README.md, and `pending.py` for the list.
2. A row in papers.jsonl is for a paper that no longer describes a deep-learning method (it
   was reclassified, merged or removed). That is reported, never fixed: the row holds curated
   quotes, and whether it should go is a judgement.
3. site_data.json or page_numbers.json no longer match what run_all.sh would produce from
   papers.jsonl, implementations.json and denovo.db (a method renamed, a year corrected, a row
   edited by hand). --fix rebuilds both.

Stdlib only, no network, well under a second: the pre-commit hook runs it with --fix on every
commit, and the check-counts workflow runs it bare as the backstop.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_site_data  # noqa: E402

NOT_YET_READ = "not yet read for this page"


def rows():
    return [json.loads(x) for x in (HERE / "papers.jsonl").read_text(encoding="utf-8").splitlines()]


def write_rows(rs):
    rs = sorted(rs, key=lambda r: r["pub_id"])
    (HERE / "papers.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs),
                                       encoding="utf-8")


def main():
    fix = "--fix" in sys.argv
    quiet = "--quiet" in sys.argv
    problems = []

    want = build_site_data.dl_method_papers()
    current = rows()
    have = {r["pub_id"] for r in current}

    missing = sorted(set(want) - have)
    if missing:
        if fix:
            current += [{"pub_id": pid, "methods": [], "title": want[pid], "metrics": [], "new_metrics": [],
                         "follows_code_of": None, "skipped_reason": NOT_YET_READ, "benchmarks": None}
                        for pid in missing]
            write_rows(current)
            print(f"check_metrics: added {len(missing)} paper(s) to papers.jsonl as '{NOT_YET_READ}': "
                  + ", ".join(f"{pid} {want[pid][:50]}" for pid in missing))
        else:
            problems += [f"publication {pid} describes a deep-learning method but has no row in papers.jsonl: "
                         f"{want[pid]}" for pid in missing]

    for pid in sorted(have - set(want)):
        problems.append(f"publication {pid} has a row in papers.jsonl but no longer describes a deep-learning "
                        "method; remove the row or fix the catalog")

    data = build_site_data.serialise(build_site_data.build())
    site_path = HERE / "site_data.json"
    if site_path.read_text(encoding="utf-8") != data:
        if fix:
            site_path.write_text(data, encoding="utf-8")
            print("check_metrics: rebuilt site_data.json")
        else:
            problems.append("site_data.json is stale: run analysis/metrics/run_all.sh")

    # page_numbers reads site_data.json from disk, so this comes after it is current. When it is
    # stale and not fixed, the numbers are reported stale too, which is true.
    import page_numbers  # noqa: E402
    numbers = page_numbers.render()
    num_path = HERE / "page_numbers.json"
    if num_path.read_text(encoding="utf-8") != numbers:
        if fix:
            num_path.write_text(numbers, encoding="utf-8")
            print("check_metrics: recomputed page_numbers.json")
        else:
            problems.append("page_numbers.json is stale: run analysis/metrics/run_all.sh")

    for p in problems:
        print(f"check_metrics: {p}", file=sys.stderr)
    if not problems and not quiet:
        unread = sum(1 for r in rows() if r.get("skipped_reason") == NOT_YET_READ)
        print(f"check_metrics: papers.jsonl covers all {len(want)} deep-learning method papers"
              + (f" ({unread} not yet read; see pending.py)" if unread else "")
              + "; site_data.json and page_numbers.json are current.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
