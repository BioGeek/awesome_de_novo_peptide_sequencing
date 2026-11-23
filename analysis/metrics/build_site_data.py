"""Derive the metrics page's site_data.json from the two curated inputs beside it.

    python3 build_site_data.py

papers.jsonl holds, per deep-learning method paper in the catalog, every metric the paper
reports and how it defines it, each with a verbatim quote and the section it came from. It
was read from the full text of the papers in the local PDF library, so it cannot be rebuilt
by a script; the quotes are there so a row can be checked against its paper.
implementations.json holds one row per tool (or per version range, where the code changed)
with the commit and file the behaviour was read at.

This script only summarises: it reads both, adds the catalog's year, method and page slug
for each paper, and writes the compact tables the page's charts and tables use. Stdlib only.
"""
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

GROUPS = {
    "peptide_recall": "Peptide recall",
    "peptide_precision": "Peptide precision",
    "aa_precision": "Amino-acid precision",
    "aa_recall": "Amino-acid recall",
    "precision_coverage_curve": "Precision-coverage curve",
    "AUC_of_precision_coverage": "Area under the precision-coverage curve",
    "FDR-based": "FDR",
    "PTM_precision/recall": "PTM precision or recall",
    "PTM_precision": "PTM precision or recall",
    "calibration metric": "Calibration",
    "mass-based metric": "Mass-based",
    "sequence similarity": "Sequence similarity",
}

# Read from each paper's own definition (papers.jsonl, field notes and definition_quote):
# the peptide-level figure is NAMED one thing and its stated denominator makes it the other.
# Only papers that state the denominator are listed; an unstated one is not a clash.
PRECISION_NAMED_RECALL = {4, 9, 20, 22, 30, 38, 120, 254, 440, 979, 1003, 1004}
RECALL_NAMED_PRECISION = {34, 52, 219}


CORE = {"peptide_recall", "peptide_precision", "aa_precision", "aa_recall"}


def residue_rule(metrics):
    """How the paper says a predicted residue or peptide is matched, read from its accuracy
    metrics only: an 'exact match' against a protein database in an FDR section is not how the
    paper scores its recall."""
    metrics = [m for m in metrics if m["normalised"] in CORE]
    if any(m["aa_tol_da"] == 0.1 and m["prefix_tol_da"] == 0.5 for m in metrics):
        return "0.1 / 0.5 Da mass rule"
    if any(m["aa_tol_da"] is not None and m["aa_tol_da"] < 0.1 for m in metrics):
        return "Stricter mass rule"
    if any(m["matching"] == "exact_string" for m in metrics):
        return "Exact sequence"
    if any(m["matching"] == "positional" for m in metrics):
        return "Position by position"
    return "Not stated"


def il_rule(metrics):
    vals = {m["il_equal"] for m in metrics}
    if True in vals:
        return "Equal"
    if False in vals:
        return "Distinct"
    return "Not stated"


def main():
    con = sqlite3.connect(REPO / "denovo.db")
    meta = {}
    for pid, name, date in con.execute(
            "SELECT p.id, a.name, p.publication_date FROM publication p "
            "JOIN publication_algorithm pa ON pa.publication_id = p.id AND pa.role = 'describes' "
            "JOIN algorithm a ON a.id = pa.algorithm_id ORDER BY p.id, a.name"):
        meta.setdefault(pid, {"methods": [], "year": int(str(date)[:4]) if date else None})
        meta[pid]["methods"].append(name)
    slugs = {}
    for line in (REPO / "slugs.lock").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "publications":
            slugs[int(parts[1])] = parts[2]

    papers = []
    for line in (HERE / "papers.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        pid = r["pub_id"]
        m = r["metrics"]
        reports = sorted({GROUPS[x["normalised"]] for x in m if x["normalised"] in GROUPS},
                         key=list(GROUPS.values()).index)
        papers.append({
            "id": pid,
            "slug": slugs.get(pid),
            "title": r["title"],
            "methods": ", ".join(meta.get(pid, {}).get("methods", r["methods"])),
            "year": meta.get(pid, {}).get("year"),
            "read": not r.get("skipped_reason"),
            "reports": reports,
            "rule": residue_rule(m) if m else None,
            "il": il_rule(m) if m else None,
            "naming": ("precision over all spectra" if pid in PRECISION_NAMED_RECALL
                       else "recall over predictions" if pid in RECALL_NAMED_PRECISION else None),
            "new_metrics": [x["name"] for x in r.get("new_metrics") or []],
            "follows": r.get("follows_code_of"),
        })

    code = json.loads((HERE / "implementations.json").read_text(encoding="utf-8"))
    for c in code:
        c["url"] = f"https://github.com/{c['repo']}/blob/{c['ref']}/{c['path']}"

    out = {"groups": list(dict.fromkeys(GROUPS.values())), "papers": papers, "code": code}
    (HERE / "site_data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                                         encoding="utf-8")
    print(f"site_data.json: {len(papers)} papers, {len(code)} implementations")


if __name__ == "__main__":
    main()
