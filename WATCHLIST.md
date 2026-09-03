# Watch list

Tools that belong in the catalog but cannot be added yet, and things deliberately
left out. Keep it short: this is a note, not a process.

Every one of the 234 `algorithm` rows has at least one linked publication, because
`publication_algorithm` is how an algorithm gets its authors, its date on the
swim-lanes, its venue and its place in the citation graph. A tool with no
manuscript would have no date to plot and an empty Authors section on its
generated page. The only repository-only entry in the catalog is
`jingbo02 Awesome-Denovo-Peptide-Sequencing`, a `kind='meta'` link collection, and
the weakest method-level precedent is DiffNovo-DIA, backed by a thesis. So a method
waits here until it has something citable.

## Waiting for a manuscript

### SemiNovo

| | |
|---|---|
| Repo | <https://github.com/grandOrgan/Seminovo> (MIT) |
| Dataset | `DarkSpec`, 4.5M unlabeled PRIDE spectra, <https://huggingface.co/datasets/PanLiu/DarkSpec> |
| Author | Pan Liu, PhD candidate, HKUST (Guangzhou) |
| First released | 2026-07-27, a single "Initial SemiNovo release" commit |
| Likely title | "SemiNovo: Learning Beyond Search-Identified Spectra for De Novo Peptide Sequencing" (the repo description; the README tagline is the looser "Learning de novo peptide sequencing models from unlabeled tandem mass spectra") |
| Last checked | 2026-08-31 |

Semi-supervised sequencing: a FlashAttention spectrum encoder with multi-scale
Fourier peak features, a causal Transformer decoder, and an exponential-moving-average
teacher generating cumulative-confidence pseudo-labels so spectra that database
search discards can still train the model. Follows the NovoBench data and evaluation
protocol, Casanovo-style beam search.

Searched 2026-08-31 with nothing found: Crossref (both title variants), arXiv,
bioRxiv, OpenAlex, web search, and the repo's own README/NOTICE. OpenAlex returns
346 hits for "seminovo" and every one is Portuguese for "pre-owned car".
OpenReview was inconclusive: keyword-matching blind submissions exist but do not
expose titles, which is consistent with a paper under review. The repo and the
dataset were created within an hour of each other and the repo has one commit, which
looks like code released to accompany a submission.

Do not be misled by the acknowledged `PanLiuCSU/CSL` repo. That is
"Semi supervised semantic segmentation in ICCV 2025", a computer-vision paper cited
for its confidence-based pseudo-label selection, not the SemiNovo manuscript.

When a manuscript appears, the classification is already worked out:
`kind='algorithm'`, `algorithm_family='Transformer (AR)'`, `is_deep_learning=1`,
`acquisition_mode='DDA'` (NovoBench benchmarks are DDA), repo as above.

### DNPS-DR (De Novo Peptide Sequencing Daily Report)

| | |
|---|---|
| Space | <https://huggingface.co/spaces/yangtingpeng/DNPS-DR> (Gradio, RUNNING on zero-a10g) |
| Author | Tingpeng Yang, Peng Cheng Laboratory / Tsinghua SIGS, already author 165 in the catalog |
| First released | 2026-08-27 |
| Last checked | 2026-08-31 |

Not a curated collection. Reading `config.py`, `scheduler.py` and `agent.py`: it
queries the PubMed eutils API once a day for a fixed keyword set (`"de novo peptide
sequencing"`, `"de novo sequencing" AND "mass spectrometry"`, immunopeptidomics, and
the tool names casanovo / helixnovo / deepnovo), feeds each day's hits to an LLM
(MiniMax-M2.5) for summarisation, and serves the result as a dated briefing. There is
a backfill routine and a 00:00 Asia/Shanghai cron.

So it is an automated literature-alert service rather than a catalog. That makes it a
`kind='meta'` candidate in the same class as `jingbo02 Awesome-Denovo-Peptide-Sequencing`
(entry 190), which is resource-backed with no manuscript, so precedent exists.

Held here rather than added because it is days old, unproven, and its summaries are
LLM-generated, which is a different kind of artifact from a hand-curated list. Revisit
if it persists and gains use. If added: `kind='meta'`, no family, no acquisition mode,
with a `publication_type='resource'` row pointing at the Space, following entry 190.

## Considered, not added

- **CorrDIA** (`10.3390/app13105969`). DIA deconvolution feeding a conventional
  database search, with no *de novo* component, so it is out of scope even as an
  adjacent tool. Revisit only if it acquires one.

## Published elsewhere, already handled

`build_versions.py` reports any tracked preprint whose bioRxiv `published` field
names a journal DOI the catalog does not have. That list was empty as of
2026-08-31; the three it found (Pairwise Attention, Modanovo, Improvements to
Casanovo) are now in. Re-run it after adding papers rather than tracking those by
hand here.
