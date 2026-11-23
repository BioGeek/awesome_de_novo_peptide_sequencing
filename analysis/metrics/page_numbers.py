"""Every number the metrics page prints, computed, with where it comes from.

    python3 page_numbers.py                 # compute all, print them, write page_numbers.json
    python3 page_numbers.py --only pmd_gg_n # one number
    python3 page_numbers.py --check         # recompute and compare with page_numbers.json

Each number is a function registered with @number(id, description, sources, made_by). `sources`
are keys of SOURCES below (the public files the number rests on) and `made_by` the files in this
folder it reads. The worked examples run small transcriptions of each tool's matching loop; every
transcription names the file and commit it was copied from, and SOURCES links to that exact line
range, so a reader can compare the two. build_pages.py turns `{{n:<id>}}` in page.qmd into a link
to the number's entry in the page's appendix, which prints the function's code. Stdlib only.
"""
import inspect
import json
import sys
import textwrap
from itertools import accumulate
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = {}
GH = "https://github.com/"

SOURCES = {
    "papers": ("papers.jsonl: every metric each paper reports, with its definition quoted",
               GH + "BioGeek/awesome_de_novo_peptide_sequencing/blob/main/analysis/metrics/papers.jsonl"),
    "code": ("implementations.json: one row per tool and version range, with the commit read",
             GH + "BioGeek/awesome_de_novo_peptide_sequencing/blob/main/analysis/metrics/implementations.json"),
    "casanovo_eval": ("Casanovo v5.2.1, casanovo/denovo/evaluate.py (aa_match_prefix, aa_match_prefix_suffix)",
                      GH + "Noble-Lab/casanovo/blob/40f4e97/casanovo/denovo/evaluate.py"),
    "casanovo_v35": ("Casanovo v3.5.0, casanovo/denovo/model.py (validation_step)",
                     GH + "Noble-Lab/casanovo/blob/ea2e875/casanovo/denovo/model.py"),
    "casanovo_fix": ("Casanovo commit 3a18fed, 'Correctly report AA precision and recall during validation'",
                     GH + "Noble-Lab/casanovo/commit/3a18fed"),
    "casanovo_masses": ("Casanovo v4.0.0 residue masses, casanovo/config.yaml",
                        GH + "Noble-Lab/casanovo/blob/3c2d3f5/casanovo/config.yaml"),
    "deepnovo": ("DeepNovo, deepnovo_worker_test.py (_match_AA_novor)",
                 GH + "nh2tran/DeepNovo/blob/8b0ec83/deepnovo_worker_test.py"),
    "instanovo_010": ("InstaNovo 0.1.0, instanovo/utils/metrics.py (_novor_match)",
                      GH + "instadeepai/instanovo/blob/43671f0/instanovo/utils/metrics.py"),
    "instanovo_100": ("InstaNovo 1.0.0, instanovo/utils/metrics.py (_novor_match)",
                      GH + "instadeepai/instanovo/blob/971c367/instanovo/utils/metrics.py"),
    "novobench": ("NovoBench, novobench/metrics/evaluate.py (curve_auc)",
                  GH + "jingbo02/NovoBench/blob/d1eff5b/novobench/metrics/evaluate.py"),
    "ranknovo": ("RankNovo, RankNovo/RankNovo/msa/tokenizer.py (TMmass, Greedymass)",
                 GH + "BEAM-Labs/denovo/blob/437f3c7/RankNovo/RankNovo/msa/tokenizer.py"),
    "proteobench": ("ProteoBench, proteobench/score/denovoscores.py (20 and 50 ppm)",
                    GH + "Proteobench/ProteoBench/blob/9a886036/proteobench/score/denovoscores.py"),
    "dnb_fix": ("denovo_benchmarks commit 3a92eba, 'fix switched aa_precision & aa_recall metric values'",
                GH + "bittremieuxlab/denovo_benchmarks/commit/3a92eba"),
}


def number(id, desc, sources, made_by=(), fmt="{:,}"):
    def deco(f):
        REG[id] = dict(f=f, desc=desc, sources=list(sources), made_by=list(made_by), fmt=fmt)
        return f
    return deco


# ---- inputs ----------------------------------------------------------------------------------

def papers():
    rows = [json.loads(x) for x in (HERE / "papers.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if not r.get("skipped_reason")]


def all_papers():
    return [json.loads(x) for x in (HERE / "papers.jsonl").read_text(encoding="utf-8").splitlines()]


def site():
    return json.loads((HERE / "site_data.json").read_text(encoding="utf-8"))


def code():
    return json.loads((HERE / "implementations.json").read_text(encoding="utf-8"))


def reports(r, *groups):
    return any(m["normalised"] in groups for m in r["metrics"])


# Residue masses as Casanovo v4.0.0's config.yaml lists them (casanovo_masses); I and L are equal.
MASS = {"G": 57.021464, "A": 71.037114, "S": 87.032028, "P": 97.052764, "V": 99.068414,
        "T": 101.047670, "C": 160.030649, "L": 113.084064, "I": 113.084064, "N": 114.042927,
        "D": 115.026943, "Q": 128.058578, "K": 128.094963, "E": 129.042593, "M": 131.040448,
        "H": 137.058912, "F": 147.068414, "R": 156.101111, "Y": 163.063329, "W": 186.079313,
        "m": 131.040448 + 15.994915,   # M+15.995, oxidised methionine
        "n": 114.042927 + 0.984016,    # N+0.984, deamidated asparagine
        "q": 128.058578 + 0.984016}    # Q+0.984, deamidated glutamine


# ---- the matching loops, transcribed ---------------------------------------------------------

def casanovo_prefix(p1, p2, cum=0.5, ind=0.1):
    """Casanovo aa_match_prefix (casanovo_eval): compare prefix masses INCLUDING the current residue;
    on a mismatch advance the peptide whose prefix mass is behind. Returns a per-position match list."""
    matches = [False] * max(len(p1), len(p2))
    i1 = i2 = 0
    c1 = c2 = 0.0
    while i1 < len(p1) and i2 < len(p2):
        m1, m2 = MASS[p1[i1]], MASS[p2[i2]]
        if abs((c1 + m1) - (c2 + m2)) < cum:
            matches[max(i1, i2)] = abs(m1 - m2) < ind
            i1, i2, c1, c2 = i1 + 1, i2 + 1, c1 + m1, c2 + m2
        elif c2 + m2 > c1 + m1:
            i1, c1 = i1 + 1, c1 + m1
        else:
            i2, c2 = i2 + 1, c2 + m2
    return matches


def casanovo_best(p1, p2, cum=0.5, ind=0.1):
    """Casanovo aa_match_prefix_suffix, mode 'best' (casanovo_eval): the prefix pass, then the same
    walk from the C-terminus over the positions before the first prefix mismatch is reached."""
    matches = casanovo_prefix(p1, p2, cum, ind)
    if all(matches):
        return matches
    i_stop = matches.index(False)
    i1, i2 = len(p1) - 1, len(p2) - 1
    c1 = c2 = 0.0
    while i1 >= i_stop and i2 >= i_stop:
        m1, m2 = MASS[p1[i1]], MASS[p2[i2]]
        if abs((c1 + m1) - (c2 + m2)) < cum:
            matches[max(i1, i2)] = abs(m1 - m2) < ind
            i1, i2, c1, c2 = i1 - 1, i2 - 1, c1 + m1, c2 + m2
        elif c2 + m2 > c1 + m1:
            i1, c1 = i1 - 1, c1 + m1
        else:
            i2, c2 = i2 - 1, c2 + m2
    return matches


def forward_only(target, pred, inverted=False):
    """DeepNovo _match_AA_novor (deepnovo) and InstaNovo _novor_match (instanovo_100): one forward
    walk, no suffix pass. inverted=True is InstaNovo 0.1.x (instanovo_010), whose second branch
    reads `elif cum_mass_a[i] > cum_mass_b[j]: i += 1`, so it advances the side that is AHEAD."""
    ma, mb = [MASS[x] for x in target], [MASS[x] for x in pred]
    ca, cb = list(accumulate(ma)), list(accumulate(mb))
    i = j = n = 0
    while i < len(target) and j < len(pred):
        if abs(ca[i] - cb[j]) < 0.5:
            n += abs(ma[i] - mb[j]) < 0.1
            i, j = i + 1, j + 1
        elif (ca[i] > cb[j]) if inverted else (cb[j] > ca[i]):
            i += 1
        else:
            j += 1
    return n


def novobench_auc(correct_in_score_order):
    """NovoBench curve_auc (novobench): precision = cumsum / rank, x = cumsum / N (recall, not
    coverage), trapezoid from the first point as sklearn.metrics.auc does."""
    n = len(correct_in_score_order)
    cs = list(accumulate(int(c) for c in correct_in_score_order))
    prec = [c / (k + 1) for k, c in enumerate(cs)]
    rec = [c / n for c in cs]
    return sum((rec[k] - rec[k - 1]) * (prec[k] + prec[k - 1]) / 2 for k in range(1, n))


GAP = 51.4238  # RankNovo's gap_penalty: every residue mass is divided by it (ranknovo)


def ranknovo_pmd(query, target):
    """RankNovo TMmass (ranknovo): edit distance where an insertion or deletion costs 1 and a
    substitution costs |mass difference| / 51.4238. The result is in units of 51.4238 Da."""
    q = [MASS[x] / GAP for x in query]
    t = [MASS[x] / GAP for x in target]
    dp = [[0.0] * (len(t) + 1) for _ in range(len(q) + 1)]
    for i in range(1, len(q) + 1):
        dp[i][0] = i
    for j in range(1, len(t) + 1):
        dp[0][j] = j
    for i in range(1, len(q) + 1):
        for j in range(1, len(t) + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + abs(q[i - 1] - t[j - 1]))
    return dp[len(q)][len(t)]


def ranknovo_rmd(query, target):
    """RankNovo Greedymass (ranknovo): for each prefix of the query, the signed offset to the
    NEAREST prefix mass of the target, in units of 51.4238 Da."""
    qm = list(accumulate(MASS[x] / GAP for x in query))
    tm = list(accumulate(MASS[x] / GAP for x in target))
    return [qv - min(tm, key=lambda t: abs(qv - t)) for qv in qm]


# ---- the papers ----------------------------------------------------------------------------

@number("n_papers", "Deep-learning method papers in the catalog considered here", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in all_papers()]

@number("n_read", "Of those, papers whose full text was read", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers()]

@number("n_unread", "Papers not read for this page: no full text in the library, or added since it was last read", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in all_papers() if r.get("skipped_reason")]

@number("n_pep", "Papers reporting a peptide-level figure (recall or precision, under any name)", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "peptide_recall", "peptide_precision")]

@number("n_aa", "Papers reporting amino-acid precision or recall", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "aa_precision", "aa_recall")]

@number("n_curve", "Papers reporting a precision-coverage curve or its area", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "precision_coverage_curve", "AUC_of_precision_coverage")]

@number("n_fdr", "Papers reporting an FDR-based figure", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "FDR-based")]

@number("n_ptm", "Papers reporting PTM precision or recall", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "PTM_precision/recall", "PTM_precision")]

@number("n_calib", "Papers reporting a calibration metric", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if reports(r, "calibration metric")]

def _rule_papers(rule):
    return [p["id"] for p in site()["papers"] if p["read"] and p["rule"] == rule]

@number("n_rule_mass", "Papers that state the 0.1 Da residue / 0.5 Da prefix rule", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return _rule_papers("0.1 / 0.5 Da mass rule")

@number("n_rule_exact", "Papers that count a residue or peptide correct only on an exact sequence match", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return _rule_papers("Exact sequence")

@number("n_rule_none", "Papers that state no matching rule at all", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return _rule_papers("Not stated")

@number("n_rule_other", "Papers with a stricter mass rule or position-by-position matching", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return _rule_papers("Stricter mass rule") + _rule_papers("Position by position")

@number("n_il_equal", "Papers that say I and L are counted as equal", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return [p["id"] for p in site()["papers"] if p["read"] and p["il"] == "Equal"]

@number("n_il_unstated", "Papers that do not say how I and L are counted", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return [p["id"] for p in site()["papers"] if p["read"] and p["il"] == "Not stated"]

@number("n_prec_named_recall", "Papers that call a peptide figure 'precision' and define it over all spectra", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return [p["id"] for p in site()["papers"] if p["naming"] == "precision over all spectra"]

@number("n_recall_named_prec", "Papers that call a peptide figure 'recall' and define it over predicted peptides", ["papers"], ["papers.jsonl", "build_site_data.py"])
def _():
    return [p["id"] for p in site()["papers"] if p["naming"] == "recall over predictions"]

@number("n_new_metric", "Papers that introduce a metric of their own", ["papers"], ["papers.jsonl"])
def _():
    return [r["pub_id"] for r in papers() if r.get("new_metrics")]

# ---- the code ------------------------------------------------------------------------------

@number("n_impl", "Implementations compared (a tool, or one version range of it)", ["code"], ["implementations.json"])
def _():
    return Rows(code())

@number("n_swapped_now", "Entries in the table whose latest code still swaps amino-acid precision and recall", ["code"], ["implementations.json"])
def _():
    latest = {}
    for c in code():
        latest[c["tool"]] = c  # rows are in version order, so the last one per tool is the latest
    return Rows(c for c in latest.values() if c["order"] == "swapped")

@number("n_forward_only", "Implementations that match residues in a forward pass only, with no suffix pass", ["code"], ["implementations.json"])
def _():
    return Rows(c for c in code() if "forward only" in c["rule"])

# ---- worked examples -----------------------------------------------------------------------

@number("ex_fwd", "PEPTIDEK against PEPGTIDEK: residues matched forward only (DeepNovo, InstaNovo 1.x)", ["deepnovo", "instanovo_100"])
def _():
    return forward_only("PEPTIDEK", "PEPGTIDEK")

@number("ex_best", "PEPTIDEK against PEPGTIDEK: residues matched with Casanovo's prefix-then-suffix rule", ["casanovo_eval"])
def _():
    return sum(casanovo_best("PEPTIDEK", "PEPGTIDEK"))

@number("ex_inv_10", "GGPEPTIDE against NPEPTIDE: residues matched by InstaNovo 1.0 and later", ["instanovo_100"])
def _():
    return forward_only("GGPEPTIDE", "NPEPTIDE")

@number("ex_inv_01", "GGPEPTIDE against NPEPTIDE: residues matched by InstaNovo 0.1.x", ["instanovo_010"])
def _():
    return forward_only("GGPEPTIDE", "NPEPTIDE", inverted=True)

@number("ex_pep_prec", "100 labelled spectra, 80 predicted, 60 matching their label: peptide precision, TP / (TP + FP)", ["casanovo_eval"], fmt="{:.2f}")
def _():
    tp, predicted = 60, 80
    return tp / predicted

@number("ex_pep_rec", "The same spectra: peptide recall, TP / (TP + FN), which Casanovo prints as pep_precision", ["casanovo_eval"], fmt="{:.2f}")
def _():
    tp, labelled = 60, 100
    return tp / labelled

@number("ex_swap_prec", "Truth PEPTIDEK, prediction PEPTIDE: amino-acid precision, correctly computed", ["casanovo_eval"], fmt="{:.3f}")
def _():
    return sum(casanovo_best("PEPTIDEK", "PEPTIDE")) / len("PEPTIDE")

@number("ex_swap_rec", "Truth PEPTIDEK, prediction PEPTIDE: amino-acid recall, which swapped code prints as precision", ["casanovo_eval", "casanovo_v35"], fmt="{:.3f}")
def _():
    return sum(casanovo_best("PEPTIDEK", "PEPTIDE")) / len("PEPTIDEK")

def _da(a, b):
    return abs(MASS[a] - MASS[b])

@number("d_kq", "Mass difference between K and Q, in Da", ["casanovo_masses"], fmt="{:.3f}")
def _():
    return _da("K", "Q")

@number("d_fm", "Mass difference between F and oxidised M, in Da", ["casanovo_masses"], fmt="{:.3f}")
def _():
    return _da("F", "m")

@number("d_nd", "Mass difference between deamidated N and D, in Da", ["casanovo_masses"], fmt="{:.3f}")
def _():
    return _da("n", "D")

@number("ppm_kq", "K against Q in ppm of K's mass (ProteoBench allows 20 ppm per residue)", ["casanovo_masses", "proteobench"], fmt="{:,.0f}")
def _():
    return _da("K", "Q") / MASS["K"] * 1e6

@number("ex_kq_pep", "Truth PEPTIDEK, prediction PEPTIDEQ: does the 0.1 / 0.5 Da rule count the peptide correct? (1 = yes)", ["casanovo_eval"])
def _():
    return int(all(casanovo_best("PEPTIDEK", "PEPTIDEQ")))

HALF = [True] * 500 + [False] * 500  # 1,000 spectra, half right, every right one ranked first

@number("ex_auc_half_nb", "NovoBench's AUC for 1,000 spectra, half right and all of those ranked first", ["novobench"], fmt="{:.2f}")
def _():
    return novobench_auc(HALF)

@number("ex_auc_half_cov", "Area under precision against coverage for the same 1,000 spectra (trapezoid, coverage = rank / N)", ["novobench"], fmt="{:.2f}")
def _():
    n = len(HALF)
    cs = list(accumulate(int(c) for c in HALF))
    prec = [c / (k + 1) for k, c in enumerate(cs)]
    cov = [(k + 1) / n for k in range(n)]
    return sum((cov[k] - cov[k - 1]) * (prec[k] + prec[k - 1]) / 2 for k in range(1, n))

@number("pmd_gg_n", "RankNovo PMD between GG and N, which have the same total mass (units of 51.4238 Da)", ["ranknovo"], fmt="{:.2f}")
def _():
    return ranknovo_pmd("PEPGGK", "PEPNK")

@number("pmd_kq", "RankNovo PMD between PEPTIDEK and PEPTIDEQ (units of 51.4238 Da)", ["ranknovo"], fmt="{:.4f}")
def _():
    return ranknovo_pmd("PEPTIDEK", "PEPTIDEQ")

@number("pmd_del", "RankNovo PMD for a missing residue, whatever its mass (units of 51.4238 Da)", ["ranknovo"], fmt="{:.0f}")
def _():
    return ranknovo_pmd("PEPTDE", "PEPTIDE")

@number("rmd_gg_last", "RankNovo RMD at the last residue of PEPGGK against PEPNK, in Da (0: the masses have realigned)", ["ranknovo"], fmt="{:.2f}")
def _():
    return abs(ranknovo_rmd("PEPGGK", "PEPNK")[-1] * GAP)

@number("rmd_gg_mid", "RankNovo RMD at the first G of PEPGGK against PEPNK, in Da", ["ranknovo"], fmt="{:.2f}")
def _():
    return ranknovo_rmd("PEPGGK", "PEPNK")[3] * GAP


# ---- driver --------------------------------------------------------------------------------

class Rows(list):
    """Implementation rows a count is made of, as opposed to a list of publication ids."""


def paper_items(ids):
    """The papers a count is made of, as appendix entries: method (year): title, linked."""
    by_id = {p["id"]: p for p in site()["papers"]}
    out = []
    for pid in sorted(set(ids), key=lambda x: (by_id[x]["year"] or 0, by_id[x]["methods"], x)):
        p = by_id[pid]
        out.append({"label": f"{p['methods']} ({p['year']}): {p['title']}",
                    "href": f"../publications/{p['slug']}.html" if p["slug"] else None})
    return out


def compute(i):
    """A number, and when it is a count of papers or implementations, the list it counts: the
    appendix prints that list under the number, so a reader can check every member."""
    r = REG[i]
    v = r["f"]()
    items = None
    if isinstance(v, Rows):
        items = [{"label": f"{c['tool']}, {c['versions']}", "href": f"{GH}{c['repo']}/blob/{c['ref']}/{c['path']}"}
                 for c in v]
        v = len(v)
    elif isinstance(v, list):
        assert len(set(v)) == len(v), f"{i}: a paper counted twice"
        items = paper_items(v)
        v = len(v)
    v = round(v, 6) if isinstance(v, float) else v
    out = {"value": v, "text": r["fmt"].format(v), "desc": r["desc"], "made_by": r["made_by"],
           "code": textwrap.dedent(inspect.getsource(r["f"])),
           "sources": [{"key": k, "label": SOURCES[k][0], "url": SOURCES[k][1]} for k in r["sources"]]}
    if items is not None:
        out["items"] = items
        out["items_kind"] = "implementations" if isinstance(r["f"](), Rows) else "papers"
    return out


def render():
    """page_numbers.json's text: every number, computed now."""
    return json.dumps({i: compute(i) for i in REG}, indent=1, ensure_ascii=False) + "\n"


def main():
    args = sys.argv[1:]
    if "--only" in args:
        i = args[args.index("--only") + 1]
        print(i, compute(i)["text"])
        return 0
    out = {i: compute(i) for i in REG}
    path = HERE / "page_numbers.json"
    if "--check" in args:
        old = json.loads(path.read_text(encoding="utf-8"))
        bad = [i for i in out if old.get(i, {}).get("text") != out[i]["text"]]
        for i in bad:
            print(f"{i}: page has {old.get(i, {}).get('text')}, now {out[i]['text']}")
        return 1 if bad else 0
    for i, v in out.items():
        print(f"{i:22s} {v['text']:>10s}  {v['desc']}")
    path.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
