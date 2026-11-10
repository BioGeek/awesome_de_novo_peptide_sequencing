# Benchmarks: denovo_benchmarks, ProteoBench and the release-order chart

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Public benchmarks

`build_benchmarks.py` reads
[bittremieuxlab/denovo_benchmarks](https://github.com/bittremieuxlab/denovo_benchmarks),
which runs 19 tools in their own containers over 86 public datasets and commits
the evaluation curves. It is the only apples-to-apples comparison the field has,
and this catalog's own data cannot produce it: everything else here describes
what a method IS.

Five tables, all written by that one builder and by nothing else:
`benchmark_dataset` (86 rows), `benchmark_tool` (19), `benchmark_result`
(1881 = one per tool, dataset and version), `benchmark_curve` (3838 = a
101-point averaged curve per tool and metric level) and `benchmark_source` (the
upstream commit, its date and the fetch date, which the site quotes).

**Two more numbers per run, read off the same curves.** `prec_full_peptide`
and `prec_full_aa` are each curve's own last point, at coverage 1, from the raw
points rather than the interpolated grid. Their meaning is fixed by upstream's
`evaluation/evaluate.py`, not guessed: the peptide curve runs over every
LABELLED spectrum with an unanswered one counted as a miss, so its last point
is correct peptides over all spectra, the papers' **peptide recall**; the
amino-acid curve runs over PREDICTED residues, so its last point is the papers'
**amino-acid precision**. Every run's curves reach coverage 1. The per-species
results ARE these curves: each `results/<dataset>/` folder holds the two
precision curves plus spectral-angle and retention-time curves that no paper
table has a counterpart for.

**What the numbers are.** `auc` in the upstream CSVs is the area under a
precision-coverage curve, which the benchmark manuscript calls AP. Two levels
are stored, peptide and amino acid. `benchmark_curve` holds the
**dataset-macro-averaged** curve: each run's curve is interpolated onto a shared
101-point coverage grid and the grid points are averaged, which is the only
correct way to average curves whose own x values differ per run. That is figure
1b/1c of the manuscript, and the site's ranking chart and heatmap are its
figure 1d.

**The AP of the averaged curve is not the median of the per-dataset APs**, and
the gap is large: InstaNovo's median AP is 0.854 and the AP of its averaged
curve is 0.736. Both are in the summary table, labelled. Averaging curves is not
averaging areas.

**Latest version, not best.** Four tools ship two versions in the results
(`_old` twins for PEAKS and Novor, two container builds each for InstaNovo and
π-HelixNovo). `version_key()` prefers the unsuffixed twin, then compares the
embedded numbers, and the chosen row is flagged `is_latest` because the rule is
not expressible in SQL. The upstream visualisation PR keeps the highest-AUC
version instead, which lets a tool use a different version on every dataset;
measured, the two rules disagree on 83 of 1629 (tool, dataset) pairs with a
median AP difference of 0.0000 and a maximum of 0.2088. Note the upstream
dashboard's own "latest" is `sorted(versions)[-1]`, which picks `12.5_old` over
`12.5`.

**Tool names are matched to `algorithm` rows, and a miss stays NULL.** Matching
normalises case and punctuation and folds `π` to `pi`, against both
`algorithm.name` and `algorithm.aliases`. `TOOL_ALIASES` in the builder carries
the four that cannot match that way, with the reasoning for each; `gcnovo` to
`Denovo-GCN` is the one that is an identification rather than a spelling, and it
rests on the container being DeepNovoV2's code plus a `model_gcn` module.
`casanovo-scaling` is deliberately unmapped: it is a scaling experiment over a
24-dataset subset, not a released tool. An unmatched tool keeps its upstream
name and simply has no link, which is visible; a wrong guess would not be.

**A partial tool is in the heatmap and out of the ranking.** Ranking a tool that
ran on 24 of 84 datasets against tools that ran on all of them would be
meaningless, so the two cross-dataset charts filter on
`n_datasets = the dataset count` while the heatmap shows every cell that exists.
Missing cells are drawn grey, because on a white-to-teal scale an absent run and
an AP of 0 are the same colour.

**The numbers will not match the manuscript** and are not meant to: its figure 1
was drawn over 29 datasets, and this is the 84 the repository holds now, with
newer containers. The site quotes the commit it was built from for exactly that
reason.

**Dataset grouping is editorial, twice.** `categorize()` is the upstream
visualisation PR's name-based categoriser plus three rules that empty its
`Other` bucket, giving 17 fine categories; `CATEGORY_GROUP` then folds those
into 8 coarse groups for the heatmap's column headers, because 17 groups over 84
columns leaves a group two columns wide and no room for its label. The fine
category survives in `benchmark_dataset.category` and in the chart's tooltip.

### The critical-difference diagram

The box plot says which method ranks better; the critical-difference diagram
([Demšar 2006](https://www.jmlr.org/papers/volume7/demsar06a/demsar06a.pdf)) says whether the ranking is evidence. Both are computed in OJS
from the same per-dataset ranks, and the statistics are worth knowing because
they are easy to get subtly wrong:

- **Friedman first.** The post-hoc comparison is only licensed if the omnibus
  test rejects "all methods are equivalent", so the section reports χ² and its
  p-value. The p-value needs a chi-square upper tail, which `chi2_sf()`
  computes as the regularised incomplete gamma, series below the crossover and
  continued fraction above it. Quoting a statistic without its p-value leaves
  the reader to look up a table.
- **`CD = q_alpha * sqrt(k(k+1)/6N)`**, with q from Demšar's table 5(a) for
  alpha = 0.05, which is a TABLE and not a formula. It is inlined in
  `bench_cd`, indexed by the number of methods. 17 methods over 84 datasets
  gives CD = 2.69 rank positions.
- **A bar is a failure to separate, not a finding of equivalence.** That is the
  misreading the diagram invites, and the prose says so. So does the other one:
  a rank discards the SIZE of a difference, so losing every dataset by 0.001 AP
  ranks exactly as badly as losing by 0.3.

`fill: "family"` on a mark whose data has no `family` field renders **nothing**,
silently: the datum is dropped rather than drawn in a fallback colour. The
diagram's leads appeared and its dots and labels did not, which cost an hour,
because `bench_cd.tools` was built by `d3.rollup` and carried only the name and
the mean rank.

## ProteoBench

`build_proteobench.py` reads
[Proteobench/Results_denovo_lfq_DDA_HCD](https://github.com/Proteobench/Results_denovo_lfq_DDA_HCD),
one JSON per submitted run, and fills four tables: `proteobench_submission`
(7 rows), `proteobench_metric` (28 = one per submission, level and match
definition), `proteobench_curve` and `proteobench_source`.

It is a different benchmark from denovo_benchmarks, not a second opinion on the
same one: **one** dataset, the published nine-species benchmark of 779,879
spectra, with each run's parameters recorded alongside its numbers. A submission
is therefore evidence about a checkpoint and its settings, not about a method at
its best.

**Four metrics that are easy to confuse**, all defined in ProteoBench's
`proteobench/datapoint/denovo_datapoint.py`:

| | |
|---|---|
| `precision` | correct / predictions MADE, at whatever coverage the tool chose |
| `recall` | correct / all spectra, i.e. precision at full coverage with an unanswered spectrum counted wrong |
| `coverage` | predictions made / all spectra; can exceed 1 at amino-acid level, where the denominator is the ground truth's residue count |
| `auc` | area under the precision-coverage curve |

**Which one you pick changes the ranking**, which is the substance of
ProteoBench's own design discussion
([#356](https://github.com/orgs/Proteobench/discussions/356)). π-PrimeNovo has
the best peptide-level precision of the seven runs and answers 88% of the
spectra; by AUC it is fourth. Precision alone rewards a tool for keeping quiet,
which is why that discussion settled on AUC as the default and argued for
precision@coverage=1 alongside it. The site's metric toggle offers all three and
says this.

Each metric comes at two levels (peptide, amino acid) and under two match
definitions: `mass`, where residues count as correct within 0.1 Da so I/L are
indistinguishable, and `exact`, which requires the sequence. Mass-based is
always the higher number.

**The scatter is ProteoBench's own main plot**, reproduced: the same metric at
peptide level against amino-acid level, on fixed `[-0.05, 1.1]` axes split at
0.5 into the four quadrants it names (Good performance, Near-miss, Low
performance, Alternative candidate). Not reproduced: its background gradient
from light at the origin to dark in the top-right, because on this page the
colour channel carries the method and two colour encodings in one frame is one
too many.

**A method has one colour in every benchmark chart.** The rank box plot, the
critical-difference diagram, both precision-coverage charts and the
ProteoBench scatter all take it from `method_color` in index.qmd. They used to
disagree: three coloured by architecture family and the two curve charts by
position in their own legend, so InstaNovo was orange in one and blue in the
next. Family colour cannot serve here, because 7 of the 18 benchmarked tools
are Transformer (AR) and their curves would be one orange; the family is in
the tooltips instead, and family colour stays in the charts that hold hundreds
of methods (the swim lane, code activity, the author graph). The 18 colours
come from the same farthest-point walk over an LCh grid as the family scale,
closest pair 21.4 deltaE, walked in algorithm-id order so a newly benchmarked
tool is appended without moving any other method's colour. Verified in the
rendered charts: Casanovo, π-PrimeNovo, DeepNovo, π-HelixNovo, AdaNovo and
ContraNovo are one colour across all five charts.

**Curves are strided, not interpolated.** ProteoBench stores up to 500 points
per curve; `subsample()` keeps at most 101 of them at an even stride, first and
last included. The other builder interpolates onto a shared grid instead, which
is wrong here: these curves do not all start at coverage 0 (one tool's
amino-acid curve starts at 0.028, which the design discussion flags), and
interpolating would invent values below a curve's first point rather than leave
the gap visible.

## Performance in release order

A section on the site, right after **How they score**: every number the papers
print on four shared datasets (nine-species, seven-species, HC-PT, and DIA's
OC/UTI/plasma), one **equal-width column per method**, the methods in order
of release date (the earliest paper describing each). It was a time axis.
That put PEAKS (2003) a decade before everything else and squeezed the field
into its last years, and even a broken axis left 2024-2026 crowded. So the
order now carries the chronology, with a faint rule and the year wherever the
year changes, and the date stays in every tooltip. The method labels are
vertical: at an angle their boxes overlapped, which `check_chart_overlap.py`
counts. The section was called *Performance over time* and was renamed once
the axis stopped being time. Its heading keeps the old anchor,
`{#performance-over-time}`, so links already pointing at it still land. denovo_benchmarks -- the living proteomics benchmark,
whose peer-reviewed results paper is not out yet -- is laid over it per species
and as the mean of the nine; ProteoBench as one pooled point under *Average*.

- **The default is every revised build, plus denovo_benchmarks.** It used to
  be *original (DeepNovo, 2017)*, where most papers report, which hid the
  independent benchmark until a reader went looking, and then *revised (main)*
  alone. Once MSV000090982 was split into its three builds, most paper points
  moved to *revised (build not stated)*, so the default became every revised
  version but balanced. denovo_benchmarks is *denovo_benchmarks selection*, a
  version of its own: its MSV000096182 deposit is 101 raw files relabelled by
  its own search, whatever its `datasets_info.py` says. ProteoBench is
  *ProteoBench selection*. The "Comparing across versions" warning fires on
  version FAMILY (`perf_family`: every revised build but balanced is one), so
  it shows by default, because denovo_benchmarks is other spectra. Point shapes
  go by family too: only seven filled symbols exist and a value outside the
  domain drops the point silently.
- **A number several papers print is one point**, its tooltip listing every
  paper, table and page it was seen in. Seven-species' 371 printed values are
  143 points: most are NovoBench's, quoted again.
- **A benchmark appears only on the metric whose definition it shares**:
  AP on AUC, `prec_full_peptide` on peptide recall and precision,
  `prec_full_aa` on amino-acid precision. Casanovo's "Prec. at Cov.=1" is
  filed as peptide recall, which it is by definition, and says so in the hover.
- **The gap between the papers and the benchmarks is the finding**, and the
  prose computes it live: the number most papers print for a method
  (NovoBench's) against denovo_benchmarks' released checkpoint. It runs both
  ways -- DeepNovo lower released, because its weights are old; InstaNovo far
  higher, because its checkpoint saw far more data -- which is the
  retrained-versus-released trap made visible.
- **A merged point's basis can be `mixed`, and that is real.** Papers disagree
  about the same number: one calls InstaNovo's 0.164 "provided by NovoBench",
  another marks it its own retraining. So `mixed` cannot be filtered on; the
  interpretation picks each method's MOST-PRINTED value instead.

Three Plot traps, all silent:

- **A stroke-only symbol vanishes under a white outline.** 'version not
  stated' was drawn as `times`, which Plot draws by stroke alone. With the
  dots' white outline a lone one was invisible, so PLMNovo's column looked
  empty, and the stray x elsewhere showed only where it overlapped a circle.
  Every shape in the version scale is now a FILLED symbol (`cross`).

- **A symbol value outside the symbol domain drops the mark.** The version
  shape is meaningful only on nine-species; HC-PT points carried version
  `HC-PT (NovoBench)` and drew NOTHING, with no error, until the symbol became
  a constant off nine-species. Same family as `fill: "family"` on a field that
  is not there.
- **`r` is a scale.** A radius computed per point is squeezed into Plot's
  default range unless the plot declares `r: {type: "identity"}`; without it
  every paper point drew at about 2 px.

Tooltips are the `.map-tooltip` div bound by DOM order, one Plot.dot mark per
source, with `sort: null` so a mark's elements stay in data order (Plot
otherwise sorts dots by radius). Verified in headless Chrome: all four datasets
draw, 0 OJS errors, and a hover on each of the three marks shows its provenance.

## Benchmark numbers on the method pages

The 15 methods with benchmark results carry a `## Benchmarks` section on their
page: median AP and median rank over the 84 datasets, plus the ProteoBench AUC
and precision for the 7 that have a submission.

**Each benchmark line names its data and links to it.** "over 84 datasets"
links to a list of all 84 on the page of denovo_benchmarks' own deposit
(MSV000096182, dataset *De novo peptide sequencing tools benchmark*), section
`#denovo-benchmarks-datasets`, built from `benchmark_dataset`. The ProteoBench
line says it runs on the nine-species benchmark and links to that dataset's
*ProteoBench selection* version (`#proteobench-selection`). The main page's
*How they score* intro links the same two places, with the dataset count read
from the data rather than written in.

These ARE baked into the pages, unlike repository stars. The rule is how often
the number moves: stars change daily, so putting them on a page would rewrite
the whole page set nightly for nothing, whereas benchmark results change when an
upstream repository commits new runs and the refresh workflow only commits when
they actually changed. And a week where they do costs 17 page renders, not 2485,
because of the next section.
