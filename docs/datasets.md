# Datasets

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Datasets, at three grains

"Trained on nine-species" identifies almost nothing, and the catalog now says
so with structure rather than prose. Four tables:

| table | the thing it holds | example |
|---|---|---|
| `dataset` | what people NAME | Nine-species benchmark |
| `dataset_version` | what models RUN ON | `revised`, `InstaNovo split` |
| `dataset_address` | where a version LIVES | `MSV000090982`, a Hugging Face repo |
| `publication_dataset` | what a paper DID with it | `introduces`, `trains-on`, `evaluates-on`, `uses` |

**500 datasets, 543 versions, 1325 addresses, 1274 publication links over 303 papers.**

### What a paper did with a dataset

`publication_dataset.role` takes four values, and two of them say what the
paper's own method did:

| role | meaning |
|---|---|
| `introduces` | the paper deposited this version |
| `trains-on` | the paper trained, pre-trained or fine-tuned **its own** method on it |
| `evaluates-on` | the paper reported **its own** method's results on it |
| `uses` | the paper ran on it, and the catalog has not recorded how |

0 links carry a role outside these four, an invariant.

**"Its own" is the whole rule.** A link joins a paper to a dataset, not a
method to a dataset, so `trains-on` would mean nothing on a method page if it
counted data a paper used to retrain a baseline: DiNovo retrains PointNovo on
eight of the nine species, and that is not DiNovo's training data. Read this
way, a DESCRIBING paper's links are its method's, and each method page carries
a `## Data` section: Trained on, Evaluated on, Deposited, and "Also used,
purpose not recorded", naming which describing paper states each one where
there are several. Publication and dataset pages split the same way.

**`uses` is the absence of a finding, so it never sits beside one.** Once a
(paper, dataset, version) has any other role, its `uses` row goes: 0 links
are `uses` beside another link for the same paper, dataset and version, an
invariant. It read 10 when first registered, every one a `uses` beside an
`introduces`, written by the miner bug below. `introduces` does sit beside
`trains-on` and `evaluates-on`, since a paper that deposits a corpus usually
trains on it too, and one paper can hold both of those for one version, its
training and test splits.
**316 links are `trains-on` and 414 `evaluates-on`.**

**The nine-species benchmark has a version per build anyone ran on**, and
they are distinguishable by number, which is the only reliable way. The main
ones:

- **original** (`MSV000081382`, DeepNovo 2017) is what a paper means unless it
  says otherwise. Its MassIVE title is "De novo peptide sequencing by deep
  learning", i.e. the deposit is the DeepNovo paper's, not a dataset release.
- **revised**, introduced by "A multi-species benchmark for training and
  validating mass spectrometry proteomics machine learning models"
  (publications 98 and 113), re-curated to remove peptide redundancy between
  species, which leaked test peptides into training in the original.
  2,838,117 spectra in Zenodo main. It is **itself two variants**, `main` and
  `balanced`, the balanced one 150 whole raw files of main (779,879 spectra)
  chosen so the species are more evenly represented; both ship in one Zenodo
  record, so "the revised benchmark" is still two things. Addresses:
  `MSV000090982`, whose record carries DATED UPDATE FOLDERS (one paper cites
  `updates/2024-05-14_woutb_71950b89`) so even that accession is not a single
  fixed object: its 2022, 2023 and 2024 releases are three builds, and the 2024
  one is still 47,640 spectra away from Zenodo main (`NINE_SPECIES.md`). Each
  build is a version of its own, `revised (MassIVE 2022)`, `... 2023` and
  `... 2024`, addressed as `MSV000090982#peak` and `MSV000090982#updates/...`;
  InstaNovo's `ninespecies_updated` IPC files are a second address of 2023. The
  BARE accession sits on `revised (build not stated)`, which is where a paper
  printing only MSV000090982, or only "the revised benchmark", belongs: 19 links
  do. `build_dataset_accessions.py` resolves a bare accession to the one
  address that is bare, so those links keep landing there, and the miner's
  `VERSION_LEXICON` sends its generic revised cues to the same version. A
  build is asserted only on evidence: Pairwise Attention prints the 2024
  folder and its counts, pi-PrimeNovo prints 2,812,194 spectra, the 2022
  count. `revised (main)` now means the Zenodo archive, and keeps
  `10.5281/zenodo.13685813` for the data; and
  `10.5281/zenodo.12926326` plus `Noble-Lab/multi-species-benchmark` for the
  construction code, marked as such in `part` because code is not an address of
  the spectra.

  **Beware the neighbouring DOI.** `10.5281/zenodo.10358625` and `...626` look
  like they belong here and do not: they are `ismaRP/PPbenchmark`, released with
  "Benchmarking the identification of a single degraded protein to explore
  optimal search strategies for ancient proteins" (publications 341 and 342).
  They were briefly attached to the revised benchmark by misreading a two-line
  report where the title prints under the NEXT accession. They are not in the
  catalog at all now: both are GitHub releases of the paper's analysis code,
  which DataCite types as Software.
- **InstaNovo split** is parquet with a fixed 499,402 / 28,572 / 111,312
  train/validation/test split and its own DOI (`10.57967/hf/3821`), which makes
  it the only version reproducible by citation alone. Its 499,402 training
  spectra are the same count `BENCHMARKS.md` records NovoBench retraining every
  architecture on.
- **ProteoBench selection**, the 779,879 spectra that benchmark scores against.

A fifth row, dIon's **representation benchmark** (publication 981), is derived
from the revised deposit and measures spectrum retrieval rather than *de novo*
sequencing, so it is a version of these spectra and not a fifth thing a
sequencing paper can mean. dIon also contributed versions of two other
datasets: Casanovo's MassIVE-KB split with a validation set carved out
(`MSKB-final (dIon)`) and a capped, run-disjoint *de novo* test set of the
Kingdoms of Life deposit, each addressed at its folder of
`alfred-n/dIon-datasets`, a 16-dataset bundle on Hugging Face.

**Provenance is an address, not a different dataset.** The benchmark is nine
unrelated third-party PRIDE submissions re-curated into one MassIVE deposit, so
those nine carry `is_provenance = 1` and name their species in `part`. They are
real datasets about honeybees and tomatoes that happen to be where these
spectra came from, and treating them as nine catalog datasets would be wrong
twice: it would invent nine rows and lose the fact that they are one benchmark.

**A NULL `dataset_version_id` is the finding, not a gap.** 80 papers use the
nine-species benchmark; 49 name the original, 29 the revised, 3 the InstaNovo
split, 1 dIon's retrieval benchmark, and **34 print only a per-species provenance accession**, which does not
determine which curated version they ran on. A paper naming two versions is counted
under each, so the parts add up to more than the whole. Inventing a version for those
would hide exactly the ambiguity the table exists to expose.

**`dataset.kind` separates the two populations, and they are empirically
distinct.** Accession reuse across the PDF library is bimodal: a couple of dozen
accessions are cited by five or more papers, and a tail of several hundred by
exactly one, the paper that deposited them. So `'benchmark'` and `'training'`
are shared resources models are evaluated or trained on, and `'deposit'` is data
a paper produced as its own result. `kind` records where a dataset came from,
and the links record how often anyone reused it.

**The tail is in, all of it.** Every accession the library yields now has a
`dataset` row, titled by its own repository, which is what makes "what data has
this field actually used" answerable at all. 30 are named by their accession
because no repository returned a title, and 13 carry an accession suffix because
repository titles collide -- two accessions of one study, or a generic title --
and merging two deposits that share a name would be wrong. `acquisition_mode` is
left NULL for all of them rather than guessed: a repository title does not say
DDA or DIA, and a wrong value would feed the site's filters.

**`role = 'introduces'` is inferred from TITLE, never from citation count.** The
tempting rule, "a deposit cited by exactly one paper was deposited by it", is
wrong: a single-citation accession is very often third-party data that one
method paper evaluated on. What distinguishes a depositor is that repositories
title a submission after the study that made it. Matching is prefix-against-
prefix cut to the shorter string, with `token_set_ratio` allowed only above 40
normalised characters, and with the repository's prefix shapes stripped and
retried (`Files for: X`, `data from "X"`, `Yeast mirror LC-MS/MS - X`). The
length guard is not optional: `token_set_ratio` ignores unmatched tokens, so a
deposit called `Casanovo` scored **100** against "Improvements to CasaNovo, a
deep learning de novo peptide sequencing...", exactly the failure the PDF
matcher hit. 29 links were promoted; both short-name cases now score 0.

**Tiers are versions.** The InstaNovo-FM corpus publishes three nested labelled
tiers, HCFM within MCFM within LCFM, plus **ACFM, the unlabelled superset,
which is not published as a tier**. ACFM is still reproducible: the
InstaNovo-FM paper lists the 92 raw accessions it was built from, so
rebuilding it means reprocessing those rather than downloading them. Those 92
are its `dataset_address` rows, every one `is_provenance = 1`: where the
spectra came from, not where the tier can be fetched.

**A training corpus lists its source projects the same way, so overlap is a
query.** MassNet's `full corpus` carries the 647 public projects of its Nature
Methods Supplementary Table 1 (601 PRIDE, 46 iProX) as provenance, with the
species in `part`. Joining two versions' addresses on `accession` then answers
"do these corpora share data" and "is this benchmark inside that training set":
MassNet and InstaNovo-FM's ACFM share three projects (PXD010154, PXD023064,
PXD046453), and the same join reproduces the four MassNet projects its own paper
says overlap its 15-species benchmark and were held out of training. Parse a
table of accessions by newline, never `str.splitlines()`: pdftotext ends each
page with a form feed, which `splitlines()` also splits on, so line numbers
taken from `sed` land somewhere else and a slice silently drops a group (it
dropped 15 of InstaNovo-FM's 24 metadata-gap accessions).

**A version may have NO address, and that is a recorded fact rather than a
missing one.** 21 of 543 versions have none, counting only addresses
that are not provenance. Three shapes: tiers that are not published but are
reproducible from the raw accessions recorded as their provenance (ACFM, and
ProteomeTools' all-confidence version, InstaNovo's AC-PT); NIST's peptide
spectral libraries, which are served from a download page and carry no
accession, so the dataset's `homepage` is their address; and the living-proteomics benchmark's **private holdouts** (11 versions:
five organism sets, multi-protease, two immunopeptidomics, single-cell HeLa 2,
PTM/phospho, and non-natural peptides). Results are reported against the
holdouts and none can be downloaded, so those numbers are not independently
reproducible. A dataset nobody can fetch still belongs in the catalog, because a
reader comparing numbers needs to know which side of that line each one is on.

**A PER-SPECIES PROVENANCE ACCESSION IS THE BENCHMARK AT NO KNOWN VERSION,
and pNovo 3 is the worked example.** Its Table 2 scores four tools over seven
columns, and its supplementary Table S1 gives the accession behind each:
PXD005025, PXD004948, PXD004325, PXD003868 and PXD004467 are five of the
nine-species benchmark's OWN provenance submissions, already in
`dataset_address`. Going to those submissions directly does not say which
curated version was used, so those columns resolve the dataset and leave the
version NULL, and the paper is counted among those naming no version.

The other two columns, QE_HF_X1 and QE_HF_X2, are two HeLa runs on a Q Exactive
HF that share ONE PRIDE submission, PXD006932, and differ only in how much of
it was used (219,698 against 314,608 MS/MS spectra). So they are two versions
of one deposit rather than two deposits. The mapping is confirmed by
arithmetic rather than by name: Table S1's `#PSMs` column reproduces the
`#Total PSMs` row of Table 2 exactly, for all seven.

**"Seven-species" is a DIFFERENT DATASET, not a version of nine-species.**
Tran et al. 2017 built two evaluation sets, and papers name them side by side:
a **low**-resolution set of seven species, assembled from seven earlier
publications and scored leave-one-out, and the high-resolution nine-species
set. Different instruments, different species, different spectra, so one is not
a curation of the other. It gets its own `dataset` row with two versions,
DeepNovo's original and NovoBench's fixed split (317,009 / 17,740 / 17,094,
yeast held out).

**"HC-PT" is a version of PROTEOMETOOLS, and the arithmetic is the proof.**
NovoBench describes HC-PT as the set "detailed in the InstaNovo paper":
synthetic tryptic peptides spanning the canonical human proteome, alternative
proteases and HLA peptides, labelled from high-confidence MaxQuant results.
ProteomeTools already carried that corpus as `high-confidence (InstaNovo)`, and
HC-PT is almost exactly a **tenth** of it on all three counts, 213,284 against
2,132,847 train, 25,718 against 257,187, 26,536 against 265,369. So it is a 10%
subsample, recorded as a version; a second `dataset` row would have split one
resource across two pages each claiming the same spectra. A paper reporting "on
HC-PT" is reporting on a tenth of that corpus under NovoBench's split.

**Except in InstaNovo's own papers, where HC-PT is the WHOLE corpus.**
InstaNovo coined the name: its HC-PT is the full high-confidence set (2.6M
spectra, best PSM per peptide), and its AC-PT is every PSM regardless of
quality (about 28M spectra over the same 742k peptides), now a version of its
own, `all-confidence (InstaNovo)`. So the same four letters name two different
sets of spectra depending on who prints them, and the miner's default mapping
to NovoBench's subsample would be wrong on InstaNovo's tables. It is never
applied there: those tables resolve each row through `ROW_DATASET` (see
**Comparison tables in the database**), which pins InstaNovo's HC-PT to
`high-confidence (InstaNovo)`.

Both NovoBench splits have **no address**, which is the point rather than a
gap: NovoBench publishes code and not data, so its retrained numbers, the ones
`BENCHMARKS.md` warns against reading beside released-checkpoint numbers,
cannot be reproduced without splits nobody can download.

**A version label is unique only WITHIN its dataset**, which the seven-species
row proved by accident: its earliest version is also called
`original (DeepNovo, 2017)`, and two `check_counts.py` queries matched on the
label alone. The nine-species count silently moved from 16 to 17 and `--fix`
rewrote the prose to agree. Both queries now name the dataset too. Any new
registry query over `dataset_version.version` must do the same.

**ProteomeTools has nine versions, and they are not re-releases.** Parts I-III,
the high-confidence InstaNovo split, its all-confidence superset, NovoBench's
HC-PT subsample of it, the 21-PTM subset, the same peptide pools
re-run on a **Bruker timsTOF**, their **non-tryptic** counterpart on that
instrument, a **TMT 6-plex** form, and the **single amino-acid variants**
PepPr evaluates on (PXD081052, private until its paper is published). Instrument and label change the fragment
ladder a model has to read, so "trained on ProteomeTools" is as
under-determined as "evaluated on nine-species", for a different reason.

### The two dataset charts

**"The data underneath"** holds the version-ambiguity bars and the nine-species
provenance flow, between the benchmark sections and the application areas, which
is where a reader has just finished comparing numbers and should learn what the
numbers were computed over.

**Only three datasets qualify for the bar chart, and that is the finding.** A
dataset earns a row when naming it is ambiguous: more than one version in use,
or at least one paper that named none. Measured, exactly three clear that bar at
any floor from 2 to 4 papers, so the threshold is a constant and **not** a
slider: a control would imply a longer list than exists. The rest of the 400-odd
versions are cited once, have one version, or are always pinned.

**`fill` with a function returning a hex string renders the wrong colours,
silently.** Plot treats the returned string as a CHANNEL VALUE and maps it
through the default categorical colour scale, so `fill: d => "#d97706"` produces
scheme colours and the hex appears nowhere in the DOM. That is how the first
version of this chart shipped looking plausible and wrong. Use a named category
plus an explicit `color.range`, which is what the chart does now and which earns
a legend for free. This is the same class of trap as `dx`/`textAnchor` being
constants rather than channels, in the opposite direction.

**The provenance flow draws its two halves differently on purpose.** The nine
source submissions fan into one curated deposit with PALE, EQUAL-height ribbons,
because the catalog stores no per-species spectrum count and sizing them would
have invented one; the deposit then fans out to the versions with ribbons
weighted by the papers naming each. A version nobody has cited still gets a
visible box at a 16 px floor, because it exists whether or not anyone cited it:
that is how `ProteoBench selection` and `revised (balanced)` appear at 0 papers.
Hand-rolled SVG rather than d3-sankey, following `application_sankey` above it.

**A third chart, "Deposits that travel together", answers a different question
with the same data.** Six of the reused deposits have NO shared provenance: they
are unrelated studies, of dolphin tissue and bronchoalveolar lavage fluid and
diabetic beta-cells, that nobody merged into anything. What they share is that
the same papers reach for all of them at once, which makes them a benchmark
suite in practice and nothing in name. So this one is a usage flow, deposit to
paper, where the nine-species chart is a provenance flow.

**The block is computed from an identical citing-paper SIGNATURE, not from
pairwise similarity**, which is what makes it worth printing: five deposits are
cited by exactly the same five papers, and a sixth by those five plus one. The
set is DERIVED from a reuse count, so a block that forms later appears without
an edit; nothing hardcodes an accession. Every ribbon weighs 1, because a paper
either used a deposit or did not and there is no spectrum count to scale by, so
node height is degree.

Two measured fixes it needed. Paper labels are truncated at 40 characters, after
three ran past the frame; and where two papers share a leading method name --
a paper describing both PandaNovo and pi-HelixNovo leads with the same string as
the pi-HelixNovo paper -- the YEAR is appended, but only to the labels that
actually collide.

All three were audited with `check_chart_overlap.py`: **39 chart SVGs, 0 OJS
errors, 0 collisions**. It caught two things eyeballing had missed. The
nine-species provenance labels were clipped by 42 px at a 232 px left margin
("Candidatus Thiodiazotropha endoloripes" is the longest), now 300. And the
VENUES chart, which this work never touched, had a 3 px overlap between its
'40' tick and its axis label, fixed by remedy 3 above (`labelOffset` plus
`marginBottom`); the previous "0 across 20 charts" note had gone stale as the
page grew to 39.

### The dataset network and the dataset table

**The network holds a dataset once two or more papers link to it**: 83 datasets,
93 papers, 279 links, 176 nodes. A deposit cited only by the study that made it
says nothing about reuse, and several hundred are in that state. Edges are
UNWEIGHTED, because a paper either used a dataset or did not and there is no
spectrum count to scale by, so "weighted by reuse" is carried by node radius.

Three force settings were measured rather than guessed, and the number to tune
against is **node spread**, not the label audit, which passes happily on a
compact knot:

- **1250 px wide, not the 1400 the other two force charts use.** At 1400 the
  nodes filled 69% of the frame with nothing within 179 px of the right edge.
  Narrowing the frame to the content beats pushing the content at the frame.
- **Link distance 78 and charge -240**, up from 58 and -170. The layout
  converged at the tighter values but did not fill: the deposit cluster holds
  about 150 of the 176 nodes and a charge capped at 190 px cannot separate a
  ball that dense.
- **The cluster centroid is WEIGHTED by group size.** A phyllotaxis walk of
  three points is not symmetric about the origin, and subtracting a plain mean
  moved the targets without moving the network, because 72 of 83 datasets are
  deposits and the centre of mass sits at that group's target. Weighting puts
  the heaviest group near the middle, which is what centring a lopsided graph
  means.

Final measurement: 176 nodes, **0 within 4 px of the frame**, 75% of the width
filled (against 76% and 79% for the other two), 101 labels shown, 0 overlaps.

It carries the same **fullscreen button and `.map-tooltip`** as the other force
charts. The SVG `<title>` it shipped with was not good enough: the native
tooltip needs a hover pause, cannot be styled and wraps nothing, so a
120-character repository title arrived as one unbroken line. **The tooltip div
must live INSIDE `.chart-wrap`**, because a fixed-position element outside the
fullscreened subtree is not rendered in fullscreen at all, so a tooltip parked
on `body` disappears exactly when the chart is most readable.

**Count node marks, not every rect and circle.** The first spread measurement
read 36 px as the leftmost node and sent me tuning a centroid for two rounds;
36 px is the LEGEND swatch, drawn outside the zoom group. Filter to marks whose
parent group carries a `<title>`, and the real span was 257 px.

**"Every dataset" is a searchable table over all of them**, which is how the
long tail is reachable at all: `Inputs.search` is given the accession column
explicitly, so typing `PXD004424` or `zenodo` finds a dataset that no chart has
room to show. A tick under *Deposited* means a catalogued paper produced the
data rather than merely running on it.

**The bars count USES, not papers.** A paper naming two versions of one dataset
contributes two rows, so the bar total is a link count: nine-species reads 44
uses across 34 distinct papers. The axis said "Papers naming this dataset" and
the table beside it said 34, and the two disagreeing was a labelling bug, caught
by reading both at once.

### build_dataset_accessions.py

Mines the LOCAL PDF library for accessions and links them. It **never** creates
a `dataset`, `dataset_version` or `dataset_address` row: deciding that a pile of
spectra is a named dataset, and which version, is the judgement call the three
tables exist to record. Unknown accessions go to `dataset_candidates.csv`,
ranked by how many papers cite them. `--write` creates only
`publication_dataset` rows, and only for accessions already in
`dataset_address`, because that half is mechanical: a paper printing
`MSV000081382` used the nine-species benchmark.

**It must never run in CI**, same reason as `build_pdf_library.py`: there is no
library on a runner. It imports that module for `rapidfuzz`, so run it under
`uv run python`, not bare `python3`.

Two traps, both hit while writing it:

- **Do not map filenames to publications through `pdf_status.csv`'s `file`
  column.** That column is written by `fetch` for files it downloads, so it
  covers neither hand-filed PDFs nor the full-title rename. It named 130 of the
  243 files present plus 82 that no longer existed, silently halving the scan:
  100 accessions found instead of 402. Call `build_pdf_library.coverage()`
  instead, which is the audited matcher.
- **A Zenodo or figshare DOI is as often software as data.** The first run put
  PyTorch Lightning 0.7.6 and two supplementary-file bundles near the top of the
  candidate list, and they would return on every run. DOI-shaped accessions are
  now checked against DataCite (cached) and dropped when the repository's own
  title says software release, supplementary material or code, with the count
  and the reason PRINTED rather than filtered away silently, since that count comes
  from the lookups and not from the database. An unknown title is never an
  exclusion. **The repository's own TYPE is checked before the title**:
  DataCite's `resourceTypeGeneral` says `Software` for every Zenodo GitHub
  release whatever it is called, and a title need not sound like code.
  "Fine-Tuning Scheduler", a PyTorch Lightning extension, passed every title
  pattern and was a dataset with its own page until a reader spotted it. An
  audit of every DOI address by type then removed seven more rows:
  - TensorFlow, InstaNovo's and Casanovo's code releases, and PPbenchmark's
    analysis release, which are all software;
  - DiNovo's code release, recorded under a DOI misread with two trailing
    digits too many, so no record existed to flag it;
  - Casanovo's nine-species weights, already checkpoint 5;
  - Winnow's hold-one-out calibrators, which are now a checkpoint row.

  **A DOI is case-insensitive**, and papers print Zenodo's in both cases, so
  `10.5281/ZENODO.6791263` was a second Casanovo dataset beside the
  lower-case one. Accessions are compared in lower case.

  **A trailing full stop is not part of a repository name.** A Hugging Face
  name may contain dots, so the pattern accepts them, and a URL that ends a
  sentence brought its full stop along. That created
  `InstaDeepAI/ms_proteometools.`, a second copy of the HC-PT corpus that was
  already the address of `high-confidence (InstaNovo)`, and
  `InstaDeepAI/InstaNovo-P.`, which is now named after its dataset card. The
  scanner strips it.

  One verdict is worth knowing about:
  `Noble-Lab/multi-species-benchmark: Revised benchmark` is excluded as a repo
  snapshot, correctly, even though that repository is how the revised
  nine-species benchmark was built. It is code, so it is not an address of the
  data.
- **Count links, not hits.** Several accessions resolve to the same link, since
  a paper listing all nine provenance submissions is one row and nine hits.
  Without deduplication the report claimed 187 rows where `--write` created 86,
  and the report was the wrong number.
- **A curated link is never second-guessed by an accession.** The miner
  proposes nothing for a dataset the paper already links, in any version or
  role: a bare accession can only add a LESS specific version, or repeat as
  `uses` what a reader recorded as `trains-on`. Its new links are always
  `uses`, because an accession says a paper touched the data, not why.
- **`sqlite3.Row` never equals a tuple.** `build_pdf_library.load_publications`
  sets `row_factory` on the connection it is handed, so every later query in
  this script returns Rows, and `(pid, vid) in set(rows)` is always False. The
  introduced-version lookup was a dict keyed by paper, which kept one deposit
  per paper and proposed every other one again as `uses`: 62 of 83 proposals
  on 2026-10-29. Build lookup sets with `tuple(r)`.
- **A corpus's source list is not a use of the corpus.** A version with more
  than `CORPUS_PROVENANCE` (20) provenance addresses is a training corpus
  listing its sources, so a paper printing one of MassNet's 647 PRIDE projects
  used that project, not MassNet; three papers had been proposed as MassNet
  users. Those claims are dropped before resolving, which also un-blocked 103
  accessions that are deposits in their own right AND a corpus source: they
  had been counted unknown as ambiguous, which is most of why the unknown
  list read 262 rather than 159.
- **A refusal is recorded, or it comes back.** `REJECTED_LINKS` holds the
  (paper, accession) pairs a reader turned down, each with the paper's own
  reason. The first: PepPr prints PXD023419 only to say it did NOT use it.
- **Supplements are read too**, PDFs and spreadsheets: every file in
  `supplements/` named after a paper, wider than `supplement_files()`, which
  takes only the numbered PDFs the table miner wants. An `.xlsx` is read as
  the zip of XML it is, with no spreadsheet library. MassNet's 647 source
  projects are a spreadsheet, so the same paper then printed nine projects
  that are deposits in their own right; a paper already linked to a version
  that lists an accession as a source gets no second link for it.

## A deposit happens once

`role = 'introduces'` on `publication_dataset` says a paper produced the data.
Two depositing papers for one dataset version is legitimate in exactly one
shape: **a preprint and its version of record**, which both introduce it, the
same convention `publication_algorithm.role` uses for `'describes'`. The
nine-species revised benchmark has that shape, publications 113 and 98, and
they are linked in `publication_version`.

More than one introducing WORK is a role error, and there was one. Folding a
duplicate dataset row into nine-species moved its publication links across with
`role = 'introduces'` for ALL of them rather than preserving each role, which
promoted publication 220 -- a benchmarking paper that merely cites the Zenodo
deposit -- to being a depositor of the benchmark. It was invisible on the page
because the entry sat beside a genuine pair.

So the invariant is registered in `check_counts.py` and must read zero, and
does: **0** dataset versions have more than one introducing work once
preprint/version-of-record pairs are collapsed.

The page also prints each paper's `publication_type`, because a preprint and
its version of record share a title and a year, and without it the legitimate
pair renders as two identical lines and reads like duplicated data. Where there
is more than one depositing paper, the section says in one line why.

## Auditing the deposit roles

After the publication-220 error, all 33 `'introduces'` links were re-scored
against the dataset they claim to have deposited. **Every one survives**, and
the two that score low are the hand-set nine-species pair, where the paper says
"multi-species" and the dataset is named "Nine-species benchmark", so a low
title score is expected. The other checks: 0 dataset versions and 0 datasets
with more than one introducing work, and **0 of 544 `'uses'` links** score high
enough to have been a missed promotion.

**326 of 354 deposits have no depositing paper, and that is correct.** A
`'deposit'` is data some study produced, and most of those studies are
third-party proteomics papers that are not in this catalog and should not be.
The audit that matters is the inverse: an orphan deposit whose name matches a
paper that IS here. Five matched, one was real -- NovoBoard deposited
`PXD055277`, scoring 100 on a prefix comparison -- and it is now linked from
both its papers.

**The other four were false, from a one-sided length guard.** `token_set_ratio`
ignores unmatched tokens in EITHER string, so the guard has to require BOTH to
be long enough. Checking only the candidate let the catalog's own Zenodo record,
"Awesome De Novo Peptide Sequencing", match two unrelated deposits at 86, and
"Peptide Sequencing with Deep Learning" match a soil-metaproteomics deposit at
87, purely on the shared words. Re-scoring the written links with the two-sided
guard changed **no** verdict, so this never corrupted the data; it would have,
had the orphan matches been applied without reading them.

## A dataset page per dataset

`build_pages.py` generates one page per `dataset` row, all **377** of them,
reachable from the Dataset column of the Every-dataset table. The page carries
what no other page can: the **version list, each version with its own
addresses**, which is the answer to "which spectra was this number computed
over".

Three things it does deliberately:

- **Addresses hang off the VERSION**, because that is their grain. A page for
  the nine-species benchmark shows `MSV000081382` under `original` and
  `MSV000090982` plus two Zenodo records under `revised`, rather than one
  undifferentiated pile.
- **Provenance is labelled as provenance.** The nine per-species PRIDE
  submissions appear under "Assembled from 9 third-party submissions", named by
  species, not beside the addresses where the benchmark itself lives. They are
  other people's studies.
- **Papers split on the role**: "Deposited by" for `introduces` and "Used by"
  for the rest, and a paper that named no version says *version not stated*
  rather than going bare. Methods come through the DESCRIBING links only, so a
  venomics paper that ran PEAKS on a deposit does not make PEAKS a method of
  that deposit.

Keyed by `dataset.id`, since `dataset.name` is UNIQUE and there is nothing to
collapse. The table's Dataset cell now points at the page and a separate
**Source** column keeps the repository one click away, because the page is the
better default and the repository is still what someone downloading wants.
