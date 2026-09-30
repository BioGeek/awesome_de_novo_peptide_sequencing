# Benchmarks

Which public benchmarks of *de novo* peptide sequencing exist, which of them this
catalog charts, and why the rest are not charted. Written 2026-09-30; the
availability column is the part that dates fastest, so it carries the evidence
that was actually checked rather than a conclusion.

Every benchmark below is also a catalogued `algorithm` row with
`kind='benchmark'` and a page of its own. This file is about their **results**.

## The two the site charts

| | [denovo_benchmarks](https://github.com/bittremieuxlab/denovo_benchmarks) | [ProteoBench](https://proteobench.cubimed.rub.de/denovo_DDA_HCD) |
|---|---|---|
| Question | how does a tool hold up across conditions | with these settings, on this data, where does it land |
| Data | 84 datasets: instruments, organisms, digests, PTMs, synthetic | one, the published nine-species benchmark, 779,879 spectra |
| Who runs the tools | the benchmark, in per-tool containers | whoever submits, with their parameters recorded |
| Results | `results/<dataset>/*.csv`, committed per dataset | one JSON per submission, committed |
| Refreshed by | `build_benchmarks.py` | `build_proteobench.py` |

Both are polled weekly by `refresh-benchmarks.yml`, which no-ops when the
upstream commit has not moved. The metric definitions, and the three ways they
are easy to misread, are in CLAUDE.md under **Public benchmarks** and
**ProteoBench**.

## The ones it does not chart, and what would have to change

| benchmark | results published as | blocker, as checked |
|---|---|---|
| **AbNovoBench** | leaderboard at abnovobench.com | The site's TLS certificate is **self-signed**: `curl` exits 35 and WebFetch refuses. The GitHub repo (`dumbgoos/AbNovoBench`) is 34 blobs of evaluation pipeline with no results file. A valid certificate, or a results file in the repo, would make this a two-hour job. |
| **NovoBench** | Table 2 of the PDF committed to its repo | 117 blobs, no CSV or JSON. And it answers a different question: see below. |
| **NovoBoard** | — | `nh2tran/NovoBoard` carries no results files. |
| **DLDN-Bench** | — | `ddz-icb/DLDN-Bench` has `create_result_csv.py`, a script that *produces* result CSVs, but no CSVs. |
| **Living proteomics benchmark** | Nature Methods Registered Report, March 2026 | Nothing to poll yet. This is the one to watch: it is explicitly designed as a continuously updated resource, and it is co-authored by 53 researchers spanning most of the groups whose tools it evaluates. |

The bar for adding one is the same bar the two charted benchmarks clear: results
published as data, at a stable address, refreshed by their owners. A leaderboard
that can only be read by a human is a page to link to, not a source to chart.

## The retrained-versus-released trap

**NovoBench's numbers are not comparable with the other two, and all three say
"nine-species".** This is the single most likely way to publish a wrong
comparison in this field, so it is written down here.

NovoBench **retrains every architecture itself**: batch size 32, 30 epochs, on
the nine-species split of 499,402 training spectra, keeping the checkpoint with
the lowest validation loss. It never evaluates released weights. denovo_benchmarks
and ProteoBench both run the checkpoint you would download.

The gap that produces is large and, crucially, runs in **both directions**:

| nine-species, peptide-level precision | NovoBench (retrained) | ProteoBench (released) |
|---|---|---|
| Casanovo | 0.481 | **0.650** |
| π-HelixNovo | 0.517 | **0.595** |
| AdaNovo | 0.505 | **0.589** |
| DeepNovo | **0.428** | 0.346 |
| InstaNovo | 0.164 | not submitted; 0.853 median AP over 84 datasets in denovo_benchmarks |

DeepNovo scores *better* retrained, because its released weights are old. That
two-way split is the tell: NovoBench measures an architecture's data efficiency
at a small fixed budget, which flatters tools whose public checkpoints are weak
and penalises tools whose checkpoints came from far more data. Released
InstaNovo was trained on **28 million labelled spectra** against NovoBench's
499,402, a 56-fold difference, and the paper notes in passing that in their run
it reached a lower training loss than Casanovo while generalising worse.

So InstaNovo at 0.164 peptide precision is not a broken run. It is a true
statement about a model NovoBench trained, and not a statement about the tool
anyone downloads. Quoting it as the latter is the error.

Two smaller versions of the same trap, both already live on the site:

- **Precision at a tool's own coverage flatters a tool that answers less.**
  ProteoBench's design discussion works this out and settles on the area under
  the precision-coverage curve; the site offers precision, precision at full
  coverage and AUC, and π-PrimeNovo moves from first to fourth between the first
  and the last.
- **The AP of an averaged curve is not the average of APs.** InstaNovo's median
  per-dataset AP is 0.854 and the AP of its dataset-macro-averaged curve is
  0.736. Both are correct; they answer different questions, and the site labels
  which is which.

## Not benchmarks, though they are often cited as one

- The **nine-species dataset** and its rebalanced successor are *data*, not a
  leaderboard. Three benchmarks above evaluate on it, which is exactly why
  "on nine-species" identifies almost nothing on its own.
- **NovoBoard** is an FDR and accuracy *framework*: a way to compute a number,
  not a published set of numbers.
- Several catalogued `kind='benchmark'` rows are one-off evaluation papers
  (monoclonal-antibody assembly, sequence ambiguity, noise and missing
  cleavages, degraded proteins). They are worth reading and are not sources of
  refreshable results.
