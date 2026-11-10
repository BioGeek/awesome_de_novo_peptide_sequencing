# Comparison tables mined from the papers

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Reading a table's structure out of its LaTeX source

`build_table_structure.py` fetches an arXiv source tarball and reads
`\multicolumn` and `\cmidrule` directly, which is the only statement of a
column grouping that involves no geometry at all. It is REPORT-ONLY: it writes
`table_structure_candidates.csv` and prints proposed `SPANNER_OVERRIDE`
entries, and edits no Python, because deciding that a given tabular is the one
behind a given PDF table is a judgement. Never in CI; it needs the network and
the PDF library.

**Its reach is limited and worth knowing before reaching for it.** Of the 25
papers with a refused table and none accepted, 7 are arXiv with source and 3
more bioRxiv, so at most 10 are reachable. Measured over the 7, it proposed 2
overrides: most "failures" are tables whose methods are ROWS, which need no
override, and the rest are caption-match misses. Its real value is the narrow,
high-value case of a method spanner over an uneven number of columns.

It did pay for itself once: it showed that publication 30's own table prints
`Transformer-DIA`, which exposed the `paper_vocabulary` bug below.

## Spot-checking the mined tables

`review_comparisons.py` builds a LOCAL page that puts every mined table beside
a picture of the printed one, cropped out of the PDF, with the refused tables
and their reasons in the same list. Checking an extraction from a CSV is
hopeless, because what needs checking is whether the grid matches the page.

    uv run --with pdfplumber python3 scripts/review_comparisons.py
    uv run --with pdfplumber python3 scripts/review_comparisons.py --ids 49,58
    uv run --with pdfplumber python3 scripts/review_comparisons.py --rejected-only

**It writes OUTSIDE the repository and is never published**, into
`comparison-review/` beside the PDF library, for the same reason
`build_pdf_library.py` writes `pdf_status.csv` there: the crops are figures
from other people's papers, so it is a private reading aid. Nothing it
produces is committed, and it must never run in CI, which has neither a
library nor poppler.

Rendering is `pdftoppm` plus a PIL crop rather than pdfplumber's `to_image()`,
since poppler is already behind this project's PDF handling. The page render is
cached per (file, page): the first version re-ran `pdftoppm` once per table and
rendered one page seven times.

`emit()` takes an optional `collect` list and appends the resolved structure to
it, so the review page renders the parse from the same code that writes the
audit row rather than reimplementing the layout logic. `block_bbox()` is used
only for the crop and never for parsing, and it is computed BEFORE the caption
vetoes, because a refused table is exactly the case a human most needs to see.

**The sign-offs are committed, in `paper_comparison_review.json`.** They
are the curated judgement that decides which parsed tables become data -- 80
approved and 44 dismissed refusals at the first full sign-off -- so the result
cannot be reproduced without them. The file holds ids, verdicts and dates
only, nothing copied from a paper, which is why it can be committed when the
crops cannot. It used to live beside the crops as `approved.json`; the script
copies that across on first run if the repository file is missing.
Reproducing the page needs three things: this repository, the local PDF
library, and `uv run --with pdfplumber python3 scripts/review_comparisons.py`. A full
run takes about 8 minutes and should end on `0 accepted, 0 rejected`, meaning
every table is signed off; anything else is a table whose parse changed.

**Three caption traps, all from sub- and superscripts the text layer sets on
lines of their own.** An unmapped glyph (`(cid:100)`, InstaNovo's ŝe_B hats)
and a short sub/superscript fragment (Pairwise's 'Casanovo_bm') each sit alone,
indented, and read as a header row that ended the caption a sentence early. The
walk now steps over both. An unmapped glyph in the caption text becomes `�`
rather than vanishing, since deleting one turned "The ▲ denotes" into "The
denotes"; `CAPTION_OVERRIDE` holds the captions only a person can restore.
Labels the text layer glues are re-spaced by one shared rule set,
`unglue_label()` ('HeLasingle-shot', 'S.Brodae', 'Exc.Yeast',
'Candidatus“Scalindua'), with `PROTECTED_CASE` names such as 'HeLa' never
split. And the page's ranking reads a cell with the miner's own `numeric()`:
stripping non-digits read '0.463(0.004)' and '0.609±0.007' as non-numbers, so
every cell printing its spread fell out of the bold/underline ranking.

**Re-rendering is incremental, at three speeds.** Almost all of a full
render's ~8 minutes is parsing PDFs, so the page stores what it parsed in
`items.json` and the cheaper paths reuse it:

| command | parses | for | time |
|---|---|---|---|
| `review_comparisons.py` | every paper | a change to the miner | ~8 min |
| `... --ids 9,17` | those papers, merged into the full page | a fix to a few papers | seconds per paper |
| `... --rewrite` | nothing | a change to how the page is drawn, or a sign-off | under a second |

A crop is also redrawn only if it MOVED: each is keyed by source file, mtime,
page and bbox in `crops.json`, and the run prints `crops: N reused, M
rendered`; `--recrop` forces the lot. Sign-offs are re-applied to every item
from `approved.json` at write time, so an approval for a paper outside `--ids`
still takes effect. A restricted run must never prune what it did not touch:
it used to delete every other paper's crops and cut the manifest down to its
own entries, so a smoke test over seven papers cost the next full run 88
redrawn crops.

**A table's id is derived from the table, not its position**: paper, page
and printed label, as in `p202-pg10-t6` or `p64-pg6-t1-nine-species`, with
`-2` for a clash on one page. It used to be the n-th item on the page, in an
order that followed each item's verdict, so turning a refusal into an accept
reshuffled the page -- MemNovo's Table 6 moved from `pg10-0` to `pg10-2` -- and
a sign-off keyed by id could have landed on a different table. None had, which
was checked before the change: all 64 recorded labels still matched. The
migration re-keyed sign-offs, crop manifest and crop files together, and a full
re-parse then generated identical ids (118 of 118, every crop reused). An id
now changes only if its label is read differently.

**While a run is in progress the page says so**: a banner with papers done of
total, elapsed and an estimate, and a 15-second reload that stops by itself
when the finished page replaces it. Before that, the page mid-render showed
the previous version as if it were current.

**Bold and underline are OURS, always**: best value and next value within each
measurement, ties included, whether or not the paper marks anything. That is
one consistent reading across tables whose own conventions differ.

**The paper's own marks are checked, not drawn**, and every mark that differs
from ours gets a footnote saying what the original table bolded or underlined,
listing only the marks that differ. That includes a paper counting its own
variants as one method: DiffuNovo underlines pi-HelixNovo, its best competitor,
where we underline its other variant DiffuNovo (Logits). That was first left
unfootnoted as a legitimate convention, and the reviewer reversed it, because a
reader comparing the picture with the grid otherwise cannot tell why they
differ. It also covers a plain mistake: CrossNovo's Table 1 bolds both
pi-PrimeNovo (0.697) and InstaNovo (0.732) for peptide recall on Tomato, and the
odd-looking 0.732 is genuinely printed, since it reproduces that row's stated
average of 0.530.

**A paper that states its own bold rule is checked against that rule.**
ScopeNovo's Table 1 caption says Setting 1 "is reported separately for
reference and excluded from the bold comparison", and checked against our
ranking over every row it drew 22 footnotes, each that sentence restated.
`RANK_EXCLUDE` names the rows a caption excludes, with the sentence; the
marks we DRAW still rank every row, and the paper's are compared with a
ranking without those rows, so a footnote appears only where a paper breaks
its own rule. ScopeNovo breaks it nowhere. The table note quotes the rule.

A tie marked only IN PART is footnoted too, naming the tied cell we add:
LIPNovo's Table 1 underlines pi-HelixNovo-dagger at 0.765 for amino-acid
precision and not pi-HelixNovo, also 0.765. Which row carries that underline
was settled by geometry rather than by eye, because the two readings disagreed:
the rect spans x 184.5-202.9 at y 215.0, on the dagger row's baseline (215.5);
the plain row's underline, at y 205.0, is in the NEXT column, under its
amino-acid recall.

Three things are not differences: an unmarked measurement, a measurement
holding a single value (nothing to rank), and missing underlines in a table
that never underlines. A lone value the paper bolded is footnoted only where
the others are explicitly not run, as in LIPNovo's Table 4, where GraphNovo's
AUC is '-'. Measured over
the whole library: 11 footnotes -- six on DiffuNovo's best-competitor
underlines, one on the Tomato cell, and four on ties marked in part.

**Margin line numbers are not the regular face.** The regular face is the
commonest numeric face on the page, and on CausalNovo's page 16 the review
template's 54 margin line numbers, set in `NimbusSanL-Bold`, outnumbered the
table's own faces. So every cell read as bold, and the page reported the
paper bolding its own Casanovo rows. Line numbers are now stripped before
counting, and a face named bold, black, heavy, medium or semibold is never
taken as the regular face while a plainer face exists. Over the whole library
that changed one table's marks, and no other.

The marks are still READ, because checking needs them. The underline is exact
-- a thin rect spanning x 355.0-377.4 under a word spanning 355.0-377.4 -- and
bold is the face that is not the page's commonest numeric face, because LaTeX
with Times renders `\\textbf` as `NimbusRomNo9L-Medi`, which no
`bold|black|heavy` pattern catches. The fonts come from a separate extraction
pass: `extract_words` splits a word wherever an extra attribute changes, so
asking for `fontname` in the parsing pass could move a cell into another
column.

**That split can cut a bold number in three.** RefineNovo sets its bold cells
as math, `$\mathbf{0.71}$`, whose decimal point comes from CMMI10 between two
CMBX10 runs, so the font pass returned '0', '.', '71', none of them the
printed cell, and no mark in its Table 6 was ever read. That is why the
seven-species part showed no footnote although the paper bolds RefineNovo's
0.09 where SearchNovo has 0.26. `join_number_fragments()` rejoins touching
fragments on one line whose joined text is a number, in its digits' face.

**One name per species, whatever a paper prints.** Across the accepted
tables, nine species and an aggregate arrive under 69 spellings: `B. sub.`,
`B.sub.`, `Bacillus` and `BacillusSubtilis` are one organism, `Clam bacteria`,
`Clam Ba.`, `C. end.` and `CandidatusEndoloripes` another. `canonical_subset()`
resolves each to the catalog's OWN name -- the nine-species benchmark's
provenance submissions record their species in `dataset_address.part` -- and so
to the accession its spectra came from. **The index is per benchmark**
(`SPECIES_BENCHMARKS`), because one species is a different submission in
each: yeast is PXD003868 in nine-species and PXD025030 in MassNet's
15-species benchmark, whose fifteen abbreviations ('A.calcoaceticus',
B.thetaiotaomicron') resolve against its own provenance. A table on any
other dataset reads the nine-species NAMES but gets no accession: the
accession is one benchmark's submission, and 30 seven-species measurements
had been given the nine-species benchmark's. Common names are an explicit table
(nothing derives 'Human' from 'Homo sapiens'); abbreviations resolve by genus
initial plus the start of the epithet; a typo (`B. subtilus`) by a near match on
the epithet; proteases likewise (`HC Chymo.` is `HC Chymotrypsin`). A subset
that is not a species -- OC, UTI, a pNovo run -- keeps its printed form rather
than being forced into one, and an aggregate keeps its own WORD: 'Average' and
'Mean' are not merged, since one paper's average may be weighted by spectra
where another's mean is the plain mean of its per-species values; only
spellings of one word are ('Average', 'AVERAGE', 'Avg.'). The review page shows the canonical name under the
printed one, and the audit records both, `subsets_canonical`, so a schema can
store the species as printed for checking and as named for comparing.

**A table that prints differences is converted, and the conversion is
marked.** TSARseqNovo's Table 1 prints only its own scores, each followed by
'vs CasaNovo' and 'vs pi-HelixNovo' rows giving its improvement. At the
reviewer's request those become ordinary Casanovo and pi-HelixNovo rows,
computed as TSARseqNovo minus the improvement, with a note under the table
saying so. This is the ONLY place the miner records a number the paper did not
print, which is why it is a registry (`DIFFERENCE_TABLES`) and not a rule, and
why every such cell carries `derived` with the expression it came from: when
the schema lands, a derived value must stay distinguishable from a printed one.
The unit was confirmed rather than assumed: subtracting in percentage points
reproduces CrossNovo's independently printed pi-HelixNovo values exactly, in
all nine species; a relative reading matches none.

**Ranking happens within a measurement, and the subset can be a ROW GROUP.**
With methods down the side, a species can head the column (CrossNovo) or a
group of rows (LIPNovo's leave-one-out Table 3). Grouping on the column alone
ranked LIPNovo's nine species against each other as if they were one
measurement.

**A row-group label is often CENTRED on its group**, and in the text layer it
then lands between the group's rows. Attaching it to the row below, as a label
set above its group would be, paired every LIPNovo species with the previous
species' baseline -- and the parse still "verified": each LIPNovo row was
right, one baseline row looked missing, and an average of the rest happened to
match the prose's +5.3%. It did not match the other two figures the prose
gives (+4.5%, +2.3%), and that mismatch was the tell that should have been
taken. A label within a quarter of the gap of the midpoint between two rows
now belongs to both; read that way every species has its pair, the printed
Mean row is 0.751 against 0.804, and all three prose figures match exactly.
**Check a parse against every number the prose gives, not the first one.**

## Comparison tables in the database

Seven `paper_comparison*` tables hold every SIGNED-OFF mined table, in two
layers, so that one record can both reproduce the table as the paper printed
it and put its numbers beside another paper's:

| layer | table | holds |
|---|---|---|
| printed | `paper_comparison` | one printed table, or one dataset part of a split one: label, page, crop box, caption, footnote, our design note, review status |
| printed | `paper_comparison_column` | every grid column, stub included, with its role (`group`, `label`, `data`, `not_recorded`) |
| printed | `paper_comparison_header` | header cells with `col_start`/`col_end` and `header_row`/`header_row_end`, so a spanner, or a cell set across two header rows, stays one cell |
| printed | `paper_comparison_row` | every printed row, its label and row-group label |
| printed | `paper_comparison_cell` | every printed cell, text exactly as printed, plus the paper's own bold, underline and not-run marks |
| standard | `paper_comparison_result` | one measurement per cell (or per part of a two-value cell): method, variant, metric, level, canonical species and accession, value on 0-1, basis and its cue |
| standard | `paper_comparison_note` | every place the paper's bold or underline differs from the ranking of the values |

Plus the view **`paper_comparison_measurement`**, which flattens a result
with its paper, method and dataset and is where a standardised table starts.

**145 verified tables from 41 papers, 7299 measurements.** The 233 refusals the
reviewer confirmed are kept as `rejected`, with their reason and no cells, so
a refusal is a recorded decision and not an absence.

**A table whose ROWS are datasets carries a dataset per measurement.**
InstaNovo's results tables list eleven to fourteen evaluation sets down the
side, and only two of them are ProteomeTools; the caption mentions
ProteomeTools, so the whole table used to be filed under it. `ROW_DATASET` in
the miner maps each printed row label, per paper, to a dataset, version and
canonical name, taken from the paper's Data Availability statement:
- HeLa single-shot, HeLa degradome, nanobodies and *S. brodae* go to
  InstaNovo's own deposit, PXD044934;
- immunopeptidomics and snake venoms go to the deposits already catalogued;
- the wound exudates and Herceptin go to deposits added for them;
- HC-PT and AC-PT go to ProteomeTools.

`paper_comparison_result` therefore has its own `dataset_id` and
`dataset_version_id`, and the measurement view reads them before the table's.
The table itself records `one per row`, and a row the registry does not name,
such as the mean, has no dataset. A deposit with a single version takes it,
while a benchmark with several stays NULL. That is why InstaNovo's
Yeast/Bacillus/Mouse rows name no version, because the paper does not say.

π-PrimeNovo's Supplementary Table 6 has the same layout, with HCC,
IgG1-Human-HC, PT and three-species down the side, and is mapped the same
way. Three-species is GraphNovo's own test set on Zenodo 8000316, the deposit
LIPNovo and LIPNovo+ also compare on.

InstaNovo-FM's Tables S12 and S13 print no accession for their "six held-out
biological validation datasets". The Methods identify them anyway: they are
InstaNovo's application sets minus "the Immuno and Herceptin datasets". Four
keep InstaNovo's names (TPL Antibodies is glossed "nanobodies"). GluC is
InstaNovo's "HeLa GluC degradome", and Hela QC is, by elimination, HeLa
single-shot. Winnow's HeLa QC is a different set. The chain of evidence is
in the tables' design note, and the rows carry InstaNovo's canonical names, so
the two papers' numbers now meet on one subset.

**A protease is a subset, and the chain belongs in it.** Digest-level tables
(CrossNovo's antibody tables, π-PrimeNovo's Supplementary Table 7) record each
digest as the measurement's subset, canonicalised by `canonical_subset()`.
That covers the paper's typo 'Chymotrysin' and the glued 'ProteinaseK'.
CrossNovo prints 'HC Trypsin'; π-PrimeNovo prints 'Trypsin' in "the
IgG1-Human-HC dataset". `SUBSET_PREFIX` adds the 'HC' that dataset name
states, which is what lets the two papers' numbers meet on one subset.
**And both papers' antibody tables are one deposit, MSV000079801.**
CrossNovo prints no accession, but its prose cites the human set as
reference [30], Tran et al. 2016, whose deposit that is. Its WIgG1-Mouse
table cites nothing, and the same deposit's MassIVE record names it: *Mus
musculus* among its species, and "IgG1 light and heavy chain digested by
Asp-N, Chymotrypsin, Trypsin", the table's exact columns.

**A per-residue table is refused (C4).** Precision on M(O), Q, F and K one
residue at a time is a different grain from a table's amino-acid precision.
Stored, "Q" would sit in the subset column beside *S. cerevisiae*.

**A crop beside running text starts at the caption.** A table set in a
wrapfigure shares its lines with prose, and the crop's left edge used to come
from that prose. When prose sits left of the 'Table N' label on the caption's
own line, the crop is bounded at the label, or at the table's own stub words
just left of it, since a centred label can be indented from its stub. Over
the whole library it moved six other crops, every one an improvement.

**Four parser faults, found on one table.** PLMNovo's Table 1 was refused
three times over, and each cause was general:
- A number is never a label with a year glued on. `0.1983` matched as `0.`
  plus the year 1983.
- A glued `ClassificationLoss` counts as a loss column, which is not recorded.
- Row groups separated by a RULE rather than by a wider gap are read by
  attaching each row to its nearest group label. That reading is kept only
  when every label sits at the middle of its group.
- **Never reuse `near` as a local name inside `emit()`.** That is its page
  text parameter, and shadowing it crashed dataset resolution on the next
  table.

Its MSKB rows are MassIVE-KB, with no version, under the split published with
Melendez et al.'s enzyme-bias data.

**dIon's tables are kept where their metric meets another paper's.** dIon
(publication 981) is a self-supervised spectrum encoder, and most of its 18
tables measure representation geometry, source recovery or AUROC, which no
other catalogued paper reports; those stay refused. Its three *de novo*
tables are kept: Table 1 (Kingdoms *de novo* and MSKB-final), Table 26
(MSKB-final with amino-acid metrics) and Table 25 (70 Kingdoms species), each
with the released Casanovo 5.2.1 and InstaNovo 1.2.2 beside dIon. Four things
they needed, each general or a named registry:
- **"Peptide precision at full coverage" is `precision@cov1`**, by the
  paper's own definition (unanswered spectra counted wrong), which the site
  already reads as peptide recall.
- **A row group can name a variant, not a subset.** Its rows are grouped by
  TRAINING corpus, so one 'dIon init., end-to-end' row is two models.
  `ROW_GROUP_VARIANT` folds each curated group label into the variant, reads
  the groups top down, and takes the first group's label from the stub, where
  the header walk puts a label set directly under the header.
- **A table can name its datasets per column** (`COLUMN_DATASET_OVERRIDE`),
  for headers the global cue list should not learn: 'Kingdoms' added there
  would move every paper whose prose mentions the Kingdoms of Life.
- **A not-run marker is a cell.** 'InstaNovo 1.2.2  0.570  --' counted as one
  number, so the row was not data and the block ended above it. And a single
  long word starting in the stub is the stub's: 'dIon-de-novo-labeled-v1
  training' ran past the stub's edge and was dropped.

Kingdoms *de novo* is dIon's own run-disjoint split of *The Proteome Landscape
of the Kingdoms of Life*, so it resolves to that deposit with no version.
MSKB-final is Melendez et al.'s released Casanovo 4.2/5.0 corpus, the deposit
catalogued as *Casanovo digestion enzyme bias data*, scored on its whole
200,000-spectrum test split; PLMNovo scores its tryptic and multi-enzyme
parts separately, so the two do not meet on a subset.

**A one-line centred caption has no pitch to stop on.** The caption walk ends
at a paragraph gap measured against the caption's own line spacing, so a
single centred line ran on into the paragraph under the table. That garbled
ReNovo's Table 8 caption and clipped its crop on the prose's right edge. Once
the caption read so far ends a sentence, a line with a word straddling the
caption's left edge stops the walk: that is the text block, not a caption
line. And a header wider than its numbers ('Peptide AUC') now widens the
crop, counting only header rows, since the prose underneath starts inside the
last column too. Over the library: no data changed and 14 crops moved, all
improvements.

**Three tables brought in eleven general rules, and two of the rules went
too far first.** SeqNovo's Table V, GA-Novo's Table 5 and the "abc to xyz"
Table 1 had all been refused. The rules that fixed them:
- **IEEE layout.** A bare centred 'TABLE V' over a wider centred caption
  takes that line whole. Small capitals arrive split ('T HE B EST') and are
  rejoined, but only in a caption with no lower-case letter. A block cannot
  run through a bare label line, so two IEEE tables set close together stay
  two tables.
- **Fake bold.** Text drawn three times arrives as '333000...999333'. It is
  collapsed and counted as the paper's bold.
- **Spaced spreads.** '0.89', '±', '0.03' becomes one cell.
- **Marker rows.** A row of significance markers ('(+) (=)') is read past.
- **Label below.** A label-only line just under an unlabelled value row
  labels it.
- **Not data.** A caption line is never data. Neither is a continuous line of
  prose.
- **Not-recorded columns.** An upper header line counts toward the
  not-recorded column test. `NOT_RECORDED_COLUMNS` names a column the header
  cannot.
- **Own method.** `TABLE_SELF` names a table's own method where the catalog
  link names another.

The two overreaches, both caught by a full re-parse before anything was
committed:
- **The label rule.** Breaking a block at ANY label line split approved
  tables on two-column pages, where the other column's 'Table 4: ...' shares
  a line with this table's rows. It is now bare labels only.
- **The prose rule.** Ten words with few numbers counted LIPNovo's PEAKS row
  (ten '-' markers) and AdaNovo's two-column row as prose, and five approved
  multi-dataset tables lost their header. It now counts only lettered words,
  and only on a line with no gap wider than a word space.

**Post-processors are mined too.** Both scripts used to read only papers
describing an `algorithm`, so RankNovo, NovoRank, ScopeNovo and 32 other
rerankers, rescorers and assemblers with a local PDF were never looked at,
though they print the same comparison tables. The selection is now
`kind IN ('algorithm', 'post-processor')`; the first pass over the 35 papers
gave 67 candidate tables, all refused or awaiting review.

**RankNovo's base models are named by the paper, and one is new.** Its
tables list six base models: the released Casanovo V2 and ContraNovo (marked
†, baseline and base model), ByNovo, RankNovo's in-house model with an
auxiliary b/y ion head (Appendix B.4), and R-Casanovo, R-ContraNovo and
R-ByNovo, "the reverse-decoding version" of each (marked ⋆, "developed and
trained by us"). ByNovo is now an `algorithm` row described by RankNovo's
three versions, and the R- models are a `reverse decoding` variant of their
base. ⋆ (U+22C6) joined the label markers. The arXiv Table 1 stacks V1 and
V2 blocks, so `ROW_VERSION` gives each method row its version positionally,
and the version is part of the duplicate key.

**A publisher's download stamp is not page text.** OUP prints "Downloaded
from https://academic.oup.com/... by guest on 16 October 2026" rotated up the
margin of every page, and on ScopeNovo's page 5 the URL landed in Table 1 as a
cell (G4). `strip_line_numbers()` now drops every word matching `STAMP` and
every rotated word in its band, and never touches upright text.

**A sentence that mentions a table is not its caption (C0).** A caption never
continues with a lower-case word. All five such "captions" in the library
were paragraphs opening "Table 3 reports...", refused before for whatever
the prose's numbers happened to trip.

**What the miner does not record is still in the printed layer.** A count
row, a BLEU row, a year or speed column, a difference row: each is a row or
column with role `not_recorded` and a `why`, holding its printed cells, so
the table renders as printed and none of it is mistaken for a measurement.
Getting there meant extending the miner, which used to throw those away
before the review payload was built.

**The header is read from the page, except where the page cannot be read.**
Most headers come straight from the miner's header walk, which needed four
fixes to be good enough to store:
- the walk is now bounded on the RIGHT by the table's own last number;
- empty rows no longer count against its four-row limit;
- touching one- or two-letter shreds are joined (`P r e c .`);
- a stub row keeps its reading order.

Six tables defeat it, the same six whose METHODS needed a curated column list:
DiffNovo's Table 1, BiATNovo's Table 2, Casanovo's Table 2, MemNovo's Table 6
and CrossNovo's two antibody tables. Their layout is transcribed from the
page in `LAYOUT_OVERRIDE`, which feeds only the printed layer. DiffNovo's
Table 1 is mis-typeset in the paper itself, with its method names wrapped
inside the metric spanners, and it is stored that way rather than tidied.

What remains imperfect is the TEXT, not the structure: where a PDF drops the
spaces, a label is stored as extracted (`Aminoacid-levelprecision(%)`). The
review page re-inserts them for display, and the stored string is never
silently "corrected". **A ROW label is re-read instead**, from pdftotext,
which keeps the spaces pdfplumber drops: `respace_from_reading()` turns
'PeaksNovo(Maetal.,2003)' into 'Peaks Novo (Ma et al., 2003)' and
'AdaNovo(w/oPSM-levelobjective)' into 'AdaNovo (w/o PSM-level objective)',
as printed. It is a second reading of the same PDF, taken only when the
label looks glued, is eight or more characters, and every place the
document prints those characters spaces them the same way. "Looks glued"
includes a join with no case change: dIon's 70 species arrived as
'Arabidopsisthaliana(callus)' and 'Bostaurus', so one unspaced token of
eight or more letters, or letters run into a bracket, counts too; a row-group
prefix ('DB ...') is tried piece by piece, since in reading order it is not
one run of text with the label.

**Our bold and underline are not stored.** They are a ranking of the stored
values within a measurement, recomputed wherever a table is drawn; storing
them would be a second copy that could disagree. The PAPER's marks are
stored, and the notes record each disagreement.

**One writer, rebuilt whole.** `review_comparisons.py --write-db` replaces
all seven tables from the parsed items and the committed sign-offs in
`paper_comparison_review.json`, and writes only signed-off tables. So the
result depends on the PDFs, the code and that file, and nothing else. These
tables are deliberately NOT in `.github/actions/commit-refreshed-db`: there
is no PDF library on a runner, and a sign-off is a human's.

**The basis search reads the PDF in reading order** (`reading_text`, via
pdftotext), not pdfplumber's page text. That text dropped narrow spaces and
read straight across two columns, so the cited sentences arrived as
'TheNine-speciesdataset,themost 6 LIPNovo:...' and appeared that way as page
tooltips. Clean text also exposed wrong bases. 'published results' had matched
the *released* rule, giving AdaNovo's quoted DeepNovo and PointNovo, and its
reproduced Casanovo, all 'released'. Pairwise's Casanovo got 'released' from
an unrelated sentence where its caption says the numbers are quoted. Those
are pinned in `TABLE_BASIS` from the paper's own words. Only the basis search
reads this text; method and dataset resolution keep theirs, and a full
re-parse changed no value, layout or verdict.

**The basis follows the paper, and every non-`unclear` basis cites its
sentence.** Invariants that must read zero, registered in `check_counts.py`:
0 verified tables without a result for the paper's own method, 0 duplicate
measurements within a table, 0 quoted results without a cue, and
0 results with a printed subset and no canonical one -- a subset that is not
a species (OC, UTI, a pNovo run) keeps its printed form as its canonical
name. That last one read 174 when first registered: left empty, a
standardised table built from the view lost its dataset column. A basis set by a
cell's legend marker cites that marker.

**A proceedings paper inherits its preprint's registry entries.**
`PROCEEDINGS_OF` in `build_paper_comparisons.py` maps 438, 439 and 440 to
their arXiv rows, and every entry keyed on the preprint (aliases, bases,
metric layouts, veto overrides) is copied to the proceedings row unless that
row has its own. Where the proceedings renumber, a label map says how, and a
label outside it is not copied: AdaNovo's NeurIPS paper drops the preprint's
PTM Table 2, so the preprint's Tables 3 to 5 are its Tables 2 to 4. The ICML
tables come out identical, cell for cell, to the preprints' approved ones;
AdaNovo's NeurIPS tables do not, since they add two baselines and ± spreads.

**Inherited entries can be wrong where a column changed meaning, and
AdaNovo's did.** The arXiv Table 1 has one Casanovo column, AdaNovo's own
reproduction. The NeurIPS table renames that column 'Casanovo (rep.)' and
adds a plain 'Casanovo' whose every value is Casanovo's own Table 2
precision at coverage 1, so the inherited "we reproduce the results of
Casanovo" called a quoted column retrained. The NeurIPS row now has its own
`TABLE_BASIS` entry. Found by comparing every preprint and version-of-record
pair measurement by measurement (method, variant, metric, level, dataset AND
version, subset): CrossNovo, RefineNovo, LIPNovo and π-PrimeNovo agree on
every shared value; InstaNovo's Nature tables replace the preprint's 'Exc.
Yeast' row with Yeast, Bacillus and Mouse, so only the means move; and
AdaNovo's own noise and PTM results were revised between versions (0.621 to
0.656, 0.793 to 0.825), as both PDFs print. Leave the version out of the key
and every revised nine-species table pairs with an original-version one and
reads as a disagreement.

**A table is only useful if its measurements can MEET another paper's**, on
the grain (dataset, version, subset, metric, level). Checked for every
verified table, a third of the isolated ones were naming, not data, and five
rules now make them meet:
- **A subset that restates the test set is no subset** (`whole_test_set()`):
  RefineNovo's 'HC-PT' and DiffuNovo's 'Nine-Species' sat where eight papers
  leave the subset empty, and NovoBench's seven-species split tests on yeast
  alone, so 'yeast' there is the whole set (`WHOLE_SET_SUBSETS`).
- **'PTM Prec.' is precision at the PTM level**, the measurement NovoBench
  tables print as 'Precision' under a 'PTM' spanner, and its level is forced:
  AdaNovo's glued 'PTMprec.' read as amino-acid level.
- **Seven-species beside HC-PT is NovoBench's split.** HC-PT is NovoBench's
  own subsample, so a table reporting both is a NovoBench table. Every
  seven-species table had been filed under no version. CausalNovo's per-species
  leave-one-out Table 8 is DeepNovo's original protocol, and pinned so.
- **A deposit with one version takes it** wherever it is resolved, not only
  in `ROW_DATASET`: pi-PrimeNovo's digest table and CrossNovo's antibody
  tables on MSV000079801 met only if versions were ignored.
- **The seven-species organisms are named**, from DeepNovo's own legend
  (*Caenorhabditis elegans*, *Pseudomonas aeruginosa*...).

What stays isolated is data nobody else reports: tables on no catalogued
dataset, a paper's own split (SeqNovo, PLMNovo, pNovo 3's HeLa runs, MassNet's
15-species benchmark), and a grain no one else prints (CrossNovo's per-PTM
table, RT-GCTnovo's whole-benchmark accuracy). InstaNovo-FM's "peptide recall"
and InstaNovo's "peptide accuracy" on the same six application sets are left
apart: InstaNovo's own figures use both words, so they are not one metric by
assertion.

### Supplements, Extended Data, and a paper's own results

Many of the field's most-cited methods print no comparison table in the main
text at all: DeepNovo, PointNovo, InstaNovo, DeepNovo-DIA, pi-PrimeNovo, PepNet
and GraphNovo keep their headline results in figures and their tables in a
supplement. Three changes reach them:

- **Labels may carry a prefix.** `LABEL_ROW` accepts `Extended Data Table N`,
  `Supplementary Table N` and `Table SN`, glued or spaced. Before, a label had
  to START with 'Table', so InstaNovo's Extended Data tables were never read as
  captions. The page locator also takes a page with a table caption, one known
  method and six decimals, because a paper's own-results table names nothing
  else.
- **Supplementary PDFs are fetched and read.** `build_pdf_library.py
  supplements --ids ...` takes what a Springer Nature article page lists as
  Supplementary Information / Results / Tables / Data, skipping the Reporting
  Summary and peer-review file, and files it in `supplements/` as
  `<paper's library name> - Supplementary N.pdf`. Spreadsheets labelled a
  table, supplementary data or source data are filed by their label
  (`- Supplementary Data 1.xlsx`, `- Source Data.xlsx`). The SUBFOLDER is the
  point: `pdfs()` reads only the root, so coverage, rename and dedupe can never
  mistake a supplement for its paper and rename it onto, or deduplicate it
  against, the main PDF. Both the miner and the review page read a paper's
  main PDF and then each supplement, and a supplement table's id carries its
  source (`p21-si1-pg26-...`), because page numbers restart. PNAS and OUP answer
  scripted requests with 403 and are reported, not worked around.
- **A paper's own results are a kind of their own.** InstaNovo's results tables
  are datasets down the side and metrics across, with the method named only in
  the caption. `orientation()` accepts such a table as `kind = 'own_results'`
  when neither axis names a method, the caption names exactly one catalog
  method (longest name first, so 'InstaNovo+' is not 'InstaNovo') and the paper
  describes it. Own results feed the measurement view, and are kept OUT of the
  method pages' Reported comparisons, because they compare nothing.

**A supplement the site should link is a `publication_supplement` row**:
the publisher's numbering, a label, the file's own title, its public URL and
page count. Paper pages list them under **Supplementary material**, and a
method page lists those of its DESCRIBING papers. The first two are the Hunt
Lab Guide's worked examples by CAD and by ETD (publication 195), which are the
substance of that paper and exist only as its supplements. MCP answers a
scripted download with 403, so the files came from Europe PMC's
`/rest/PMC11665681/supplementaryFiles` archive, byte-identical to the
publisher's. **The CAD/ETD label lives in the row, not in the filename**:
`supplement_files()` sorts on the number right before `.pdf`, so the library
names them plain `... - Supplementary 1.pdf` and `... - Supplementary 2.pdf`.

**Tables that are images are read by two vision models, and kept only where
they agree.** `image_tables.find_image_tables()` takes a table caption with a
text-free gap of 60 pt or more beside it, the gap holding images or vector
drawings: the shape of InstaNovo's published Extended Data tables, whose text
layer has a caption and a footnote and nothing between. **The gap above a
caption counts only when no text table follows it**: Aytun's thesis captions
its tables from above, and Table 4.2's own cells sit right under its caption,
so the band above it, holding only the page watermark and Table 4.1's last
rule, was cropped as an empty "image table". A line with two column gaps over
12 pt within three lines of the caption is a text table, and the finder
leaves it alone. **The mirror case holds too**: NovoRank's thesis captions
from BELOW, and the band under 'Table 5. The clustering results', holding only
the university seal, was cropped the same way, so a gap below a caption counts
only when no text table ends just above it. Of the six image candidates, that
removed three empty or figure crops and kept InstaNovo's two Extended Data
tables and Aytun's Table 4.4. The review page crops
each one and lists it in `image_tables.json`;
`read_table_images.py --reader glm` and `--reader paddle` read the crops, each
in its own environment, into `vlm-cache/`. The table is resolved only when the
two grids have the same shape and EVERY cell matches (whitespace and case
aside), and then it goes through `emit()` like any text table, marked
`extraction = 'image'` all the way into `paper_comparison`. A disagreement is a
refusal naming the cells; a missing reading is `V0`, waiting. Either way a
person signs it off against the crop.

Two environment facts, both measured. GLM-OCR runs on transformers 5.
PaddleOCR-VL's own code calls `create_causal_mask(inputs_embeds=...)`, the
transformers-5 name, while needing `ROPE_INIT_FUNCTIONS['default']`, which
transformers 5 removed: no released version runs it as shipped (4.55, the
version its config names, and 4.57 both fail). `read_table_images.paddle_overlay()`
builds `~/.cache/paddleocr-vl-tf4` -- symlinks to the clone plus a copy of the
modeling file with that one argument renamed -- and it runs on 4.57.

The models also CHECK the text tables: `read_table_images.py --crosscheck`
reads every accepted or signed-off text table's crop, and
`crosscheck_tables.py` compares the parse with both readings ROW BY ROW, in
column order. Numbers are compared by value, so GLM's '0.7540' is the
printed '0.754'. Rows the miner DERIVED, not printed (TSARseqNovo's
Casanovo rows), are skipped. Both models agreeing with each other and not
with the parse (`CHECK`) points a person at a cell; it never edits anything.

Run over all 100 accepted and signed-off text tables: **86 AGREE, 14
ONE-READER, 0 CHECK, 0 DIFFER**. Every extracted number is confirmed by at
least one model and contradicted by neither. Each one-reader case is a model's
slip: PaddleOCR-VL dropping columns or reading 1.1 for 0.1, or GLM merging two
rows. A PaddleOCR-VL pass over all of them takes over two hours on the 8 GB
card, longer than one background job may run, but readings are cached per
table, so a restart picks up where it stopped.

**A chart that prints its values is read like a table, from the text layer.**
`FIGURE_TABLES` registers such a figure by its LAYOUT only:
- the page;
- the legend order, which is the bar order within each category;
- each panel's x-range and metric;
- the categories to leave out.

`bar_figure_grid()` reads the bar labels, assigns each label to its nearest
x-axis category, and orders the labels left to right. A category with the
wrong number of labels refuses the figure (F1), since one missing label would
shift every later value into the wrong series. Rotated labels come out of the
text layer reversed (`1489.0` is 0.9841) and are turned back. Nothing is
estimated from bar heights, and the result is marked `extraction = 'figure'`.

One entry so far: Deep Novo A+'s Fig. 3, which compares DeepNovo, DeepNovo
with each of A+'s two changes alone, and A+ itself, on three test-length
splits. Its 'train' category is accuracy on the training set and is left out.
It is opt-in per figure because most charts print no values.

That entry is also the worked example of a `veto`. Its data are the
nine-species benchmark's yeast submission (PXD003868), split at random rather
than held out, so the reviewer refused it: it supports only DeepNovo against
Deep Novo A+ inside that paper. A vetoed entry stays in the registry, so the
reading and the reason are both on record, and yields a refusal with no
measurements.

What still cannot be read is the TABLE THAT IS AN IMAGE WITH NO CAPTION IN THE
TEXT LAYER. DeepNovo-DIA's supplementary
tables are not in its Supplementary Information PDF at all: they are six Excel
files labelled "Supplementary Table 1-6". The fetcher now takes an `.xlsx`
whose label says Supplementary Table, saved under that label
(`supplement_sheets()` finds them). Read, none of the six is a comparison:
dataset statistics, per-feature predictions, immunoglobulin and variant
peptides. DeepNovo-DIA's comparisons exist only as figures.

**A chart that prints NO values is read from the paper's source data**, and
MassNet (publication 989) was the day a sheet held results. Every comparison
it makes is a bar chart with nothing printed on the bars, and the publisher's
spreadsheets carry every number: Supplementary Data 1 for Supplementary
Fig. 3 (nine-species, HCC, PT) and the Source Data file for Fig. 4b (the
15-species benchmark). `SOURCE_DATA_TABLES` registers each figure by file,
sheet, and which columns hold method, subset and value, plus where the
figure is drawn so the review crop shows the bars to check the sheet
against. `read_xlsx_sheet()` reads an .xlsx with the standard library, since a
zip of XML is not worth a dependency, and shows a number at the 15
significant digits the sheet displays, because the file stores
`0.59599999999999997`. The result is `extraction = 'source-data'`.

Three things a source-data sheet gets wrong, all in MassNet's, and none of
them corrected silently:
- **The column name is not the metric.** The sheets call it `Pep_Precision`;
  the axes and the prose say peptide recall, and the prose's numbers are the
  sheet's rounded. `TABLE_METRIC` decides, with that reasoning.
- **The method name is not the legend's.** The sheets say InstaNovo where the
  legend, the prose and the version run (1.1.0) say InstaNovo+, so the entry
  maps it with `rename`.
- **A sheet can have fewer rows than the figure has bars.** Supplementary
  Fig. 3b and 3c draw π-HelixNovo and the sheets have no row for it. It is
  left unrecorded rather than read off its bar, and the table's note says so,
  as it does for each place the prose quotes a different number (PT 0.62
  against the sheet's 0.605).

### On the algorithm pages

**32 methods carry a `## Reported comparisons` section**, placed after
`## Benchmarks` so a reader meets the independently run numbers first. One
STANDARDISED table per printed table (or dataset part): methods down the side,
species then metric across, every value on 0-1 at the
printed precision (`0.530` stays `0.530`; a percentage table gains two
decimals). The dataset heads the table and links to its page; every method
links to its page. Bold and underline are our ranking per column.

**Two more sections follow it, both from the same tables.** `## Own
results` holds a paper's tables of its own method alone (`kind =
'own_results'`, InstaNovo's results tables), which "Reported comparisons"
leaves out because they compare nothing; before this they reached no page at
all. `## Compared against in other papers` holds every verified comparison
in which the method is a BASELINE, i.e. present in a table whose paper does
not describe it: the half of the evidence no other page collects. InstaNovo
is in 55 such tables from 15 papers and its page showed none. It is grouped by
paper, newest first, each paper a collapsed `<details>`, because Casanovo is
a baseline in over a hundred tables; a preprint and its version of record
fold into one entry by the same duplicate rule. Publication pages carry no
table section, by the owner's choice.

It is built from `paper_comparison_result` alone and never from the printed
header, which is why DiffNovo's mis-typeset Table 1 comes out as clean as any
other. Three rules worth knowing:

- **A table lands on every method its paper DESCRIBES** (`is_self`), not just
  the first: LIPNovo+'s paper describes LIPNovo too, so its tables appear on
  both pages.
- **A table printed twice is shown once.** Two tables are the same when every
  method, variant, metric, level, species and value matches; the copy from the
  most authoritative publication is kept (peer-reviewed, conference,
  postprint, preprint, thesis, then the later date) and the other is named
  under it. The BASIS is left out of that comparison, because it is our
  reading of each paper's prose: CrossNovo's two preprints print the same
  Table 2 and only one's prose says 'retrained'. Tables that differ in any
  number are both shown -- BiATNovo's two preprints are, since one adds PepNet.
- **Measure first, then species.** The header's top row is the measure,
  level first ('Amino acid recall', 'Peptide precision'), one merged cell
  over all of its columns. The species sit in the row beneath, in the paper's
  order. Measures follow where each first appears in the printed table.
  Grouping by species first had split one measure across the whole width.
- **A wide table is stacked.** Past 12 data columns, the columns are split at
  measure boundaries into tables stacked one above another, with the same
  rows and corner. A single measure wider than 12 is split on its own.
  Casanovo's Table 2, five measures over nine species and 45 columns, is five
  tables of nine. A row with no value in a part is left out of that part.
- **The paper's marks are named in the table's own terms.** A note reads
  'column amino acid precision: the original table underlined pi-HelixNovo
  (0.765)', with the measure as in the header and the method by its catalog
  name. Before, it was 'precision / amino acid' and the paper's abbreviation
  ('HelixNovo'). The printed form is added only where two rows would
  otherwise read the same.
- **A basis is shown on baselines only.** It says how a paper got a number
  it did not produce. On the paper's own method it said nothing true:
  'LIPNovo · retrained' quoted a sentence about retraining Casanovo.
- **Escape an asterisk in a note.** The page is Markdown around this HTML,
  and Pandoc read the footnote markers in '0.491 / 0.725*' and '* Indicates
  ...' as emphasis: they paired up, vanished, and italicised the text
  between. Notes print `&#42;`.
- **A second metric in one printed cell is a note, not a column.** DiffNovo
  prints PepNet's Plasma cells as '0.491 / 0.725*' and '0.550/0.530*/0.664+',
  the marked numbers being other metrics, some quoted from PepNet's paper.
  Given rows and columns of their own they read as a misaligned table. The
  cell's own value stays in the grid, and a note under the table quotes the
  printed cell and the footnote sentence that explains its markers. The
  database keeps all three measurements; only the page changes.
- **The basis is not a column.** It is our reading of the paper's prose and
  not part of the printed table; as a column of its own it had no header and
  read 'unclear' down most of its length. Where a paper does say how a method
  was run, the word follows the method's name, with the licensing sentence as
  its tooltip, and 'unclear' shows nothing.
- **Bootstrap classes, not custom CSS.** `custom.scss` is in the publish's
  global render key, so styling the tables there would force a full render of
  every page for a section on 22 of them.

    uv run --with pdfplumber python3 scripts/review_comparisons.py --write-db            # full re-parse, ~8 min
    uv run --with pdfplumber python3 scripts/review_comparisons.py --rewrite --write-db  # from stored items, seconds
