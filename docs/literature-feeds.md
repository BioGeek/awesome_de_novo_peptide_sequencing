# Finding what the catalog is missing: citations, candidates, feeds

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## The OpenAlex key, and the Space that watches the literature

`openalex_key.py` reads `OPENALEX_API_KEY` from the environment or from a
gitignored `.env`, and `with_key(url, params)` adds it **only** when the
request is going to `api.openalex.org`, so a builder that also calls Crossref,
Europe PMC or ORCID through the same helper cannot leak it to them. Wired into
`build_publication_impact.py`, `build_journal_metrics.py`,
`build_candidates.py`, `build_abstracts.py` and `build_affiliations.py`.

**A builder reading the key is not the same as CI having it.** The repo holds
an `OPENALEX_API_KEY` Actions secret, but a step sees it only if it maps it into
`env:` itself. For weeks only `refresh-denovo-radar.yml` did, so
`refresh-publication-impact` ran anonymous and aborted on run 38142008703 after
ten empty lookups, at publication 700 of 982. Every workflow that calls
OpenAlex now passes it: publication impact, journal metrics and candidates.
Absent, nothing raises: every builder worked without a
key before and still does, so an unkeyed clone is slower, not broken.

The key exists because OpenAlex's anonymous pool is a daily budget shared
across every caller from one IP, and a heavy day exhausts it.
`build_publication_impact.py` hit that mid-run after a bulk paper import: it
aborted on ten consecutive empty lookups, which is the right behaviour -- it
preserved the ids it had rather than writing nothing over them -- but the new
papers were left with no `openalex_id`, and **`build_affiliations.py` silently
skips any publication without one**. That ordering is the thing to remember:
impact first, then affiliations.

### BioGeek/denovo-radar

<https://huggingface.co/spaces/BioGeek/denovo-radar> is a Gradio Space showing
recent de novo peptide sequencing papers with each one marked against this
catalog, so the gaps are visible. `refresh-denovo-radar.yml` runs its
`harvest.py` weekly, commits `data/papers.json` into the Space, and commits
nothing here.

**The harvest runs in CI, not in the Space, and that is the design.** A Space
that schedules its own work writes into container storage: the results never
reach its git repo and do not survive a restart. The feed that inspired this
one does exactly that, which is why its published data stopped moving while the
Space itself kept running.

Two secrets are needed and this repo cannot create them: `HF_TOKEN` with write
access to BioGeek, and optionally `OPENALEX_API_KEY`. The workflow fails with
an explicit error rather than a confusing one when `HF_TOKEN` is absent.

**The filtering is the point, not the fetching.** A keyword feed for "de novo"
is mostly a different field: of 682 records fetched on the first run, 551 were
not de novo sequencing of a peptide at all, 14 were Zenodo or MassIVE deposits
of model weights and datasets (which match the topic perfectly and are not
papers, and one of which was this catalog's own Zenodo record), 5 sequenced
another analyte and 1 was de novo design. 45 survived, 35 of them already
catalogued, leaving 10 new. Versioned DOIs are collapsed (`10.17044/x.v1` onto
`10.17044/x`) and the catalog is matched on title as well as DOI, since the
same paper arrives under an arXiv DOI in one source and a conference DOI in
another.

**Pin gradio, and pin it forward.** The Space's `requirements.txt` asked for
`gradio>=4.44` and let pip resolve `huggingface_hub` freely, which is the one
combination that cannot work: gradio 4.x's `oauth.py` does `from
huggingface_hub import HfFolder`, and `huggingface_hub` 1.0 removed `HfFolder`,
so the Space died at `import gradio` before reaching a line of its own code.
It is pinned to `gradio>=6.29,<7` on python 3.12 rather than to an old
`huggingface_hub`, because 6.x does not import `HfFolder` at all and so stays
installable as the hub moves. `sdk_version` in the README frontmatter has to
agree: it is what the builder installs, and `requirements.txt` cannot override
it downwards.

**The list is a FEEDBACK LOOP, and closing it needed both files.** The Space
was re-proposing work already argued about: a paper added to the catalog stops
being new by itself, because `harvest.py` matches the catalog by DOI and
normalised title, but a paper REJECTED has no catalog DOI and came back every
week. `refresh-denovo-radar.yml` now passes `--watchlist ../WATCHLIST.md` as
well as `--catalog ../denovo.db`, and rejections are dropped by DOI, the same
contract `build_candidates.py` has. **Matching is DOI-only on purpose**: a
watch-list entry argues about a paper in a sentence, and fuzzy-matching those
sentences against titles would drop papers nobody rejected. An entry with no DOI
is not enforceable there, which is a reason to record one when rejecting
something. **A DOI the catalog holds is never a rejection**, because WATCHLIST.md
also cites papers for context: the AlphaNovo entry names FastaLake's DOI, and
the harvest dropped FastaLake as "already rejected" the day it was catalogued.
InstaNovo's preprint and the cricket hydrolysate paper were cited the same way.
The harvest now reads the catalog first and exempts its DOIs.

**Name the repository that PUBLISHES a checkpoint, not where you found it.**
Both π-PrimeNovo checkpoints were first credited to the ProteoBench discussion
that led me to them; they are published by `PHOENIXcenter/pi-PrimeNovo`, the
MassIVE model in its top-level README and the phosphorylation model in
`pi-PrimeNovo-PTM/`, both MIT. The repository also described the model better
than the thread did: fine-tuned from `model_massive.ckpt` on 2020-Cell-LUAD,
predicting one extra token `B` for Phosphorylation (+79.97). A provenance table
that cites a discussion thread is recording my search history, not the
provenance.

**Fixing that exposed a worse bug: the harvest REPLACED the list.** Two runs a
week apart fetched 682 and 435 records over the same window, because neither
Europe PMC nor OpenAlex returns a stable set, so an overwrite would have
silently dropped twenty papers from the Space. The list is now cumulative:
earlier entries are carried forward and re-filtered, so it shrinks only when a
paper is catalogued or rejected, and `counts.carried_over_from_last_run` says
how many are not from this week. Verified end to end against the real catalog
and watch list: 45 in, 44 out, 1 dropped as already rejected, 20 carried over,
44 of 44 catalogued, 0 new.

**The Space's refresh button is a link, not an API call.** "Run a new fetch on
GitHub" opens this workflow's page, where GitHub already limits **Run workflow**
to people with write access. Dispatching from the Space itself would need a
GitHub token stored there plus a Hugging Face login gate to keep visitors from
starting runs. The Space's date reads "List last changed", not "Generated":
the workflow commits nothing when the papers are unchanged, so the date only
moves when the list does.

**A paper nobody has ruled on opens an issue**, "denovo-radar: papers to
screen", assigned to the repository owner: the Space and the job summary are
read only by someone already looking. It is the same step as the DNPS-DR
feed's, with its own title, so the two lists never mix. The harvest has
already dropped what WATCHLIST.md and `screening_decisions.tsv` reject, so
what fires it is a kept paper with `in_catalog` false. While the issue is
open a later run only comments with papers it does not yet name. Closing it
by hand decides nothing: catalogue each paper or record its rejection, or the
next harvest proposes it again. **The run closes it**, with a comment, once the
harvest keeps no paper that is not catalogued, which is the loop's last step:
FastaLake and the camel leukocyte paper went from issue #2 to the catalog, and
the next run closed #2.

**Three harvest bugs surfaced on the button's first day**, all fixed:
- `--since` (and the workflow's `since` input) takes a date or a number of days
  back. A bare number used to reach Europe PMC as `FIRST_PDATE:[20 TO ...]`,
  the year 20, and fetch the whole literature up to the page cap.
- **The page caps truncated silently.** Europe PMC stopped at exactly 1000
  records, newest first, so the oldest vanished. A ten-year window is about
  5100 records; the caps are now 6000 and 3000 and print a WARNING when hit.
- **Titles were compared with their HTML entities**, so "synthesis &amp; de
  novo" and its DOI-less copy were two papers. Titles are unescaped and
  stripped of markup wherever they are compared, catalog included, and a
  record with a DOI registers its title so a DOI-less copy is dropped.

And one in this repo: the publish step's deliberate `exit 7` ("only the date
moved") ran under GitHub's `bash -e`, so the step died before `rc=$?` and a
quiet week failed the workflow. It is caught on the same line now,
`python3 - <<'PY' || rc=$?`.

**The ten-year backlog was curated once, on 2026-10-04.** A `--since 3650`
harvest kept 259 papers, 83 of them not in the catalog. 67 went in as
publications 447 to 513, under 50 new or existing method rows. Seven of them
are preprints of papers already here or added in the same pass, linked
through `publication_version`. 16 stayed out:
- 11 Wiley open-review records of publication 419;
- two IUPAC Gold Book entries;
- a nanopore genome assembly and its Research Square twin;
- the nanopore amino-acid paper (the non-MS boundary) and a polyketide
  siderophore paper, both recorded in WATCHLIST.md.

The first three shapes are now dropped by the harvest itself. Authors were
matched by ORCID, then OpenAlex id, then exact name only where nothing
contradicted it, and every same-name reuse and near-miss spelling was
read before writing. Eleven spellings of people already catalogued were
pinned to their rows ("A.T. Lebedev", "Koen C.A.P. Giesbers").

**A citation sweep added 343 more, on 2026-10-04.** The 30 most-cited
`algorithm`/`post-processor` rows (by OpenAlex citations to their DESCRIBING
papers, PEAKS first) were walked in order, taking every citing work that says
"de novo" in its title or abstract and is neither catalogued nor in
WATCHLIST.md: 555 candidates. The pools saturate fast, so 30 methods is the
neighbourhood: the last eight added 14 between them. Six agents screened them
against the bar in BRIEF form (method, application, review and false-friend
rules), with read-only access to the catalog: 391 IN, 10 VERSION, 154 OUT.
Inserted: the 334 high- and medium-confidence INs and the 10 versions, less
one duplicate OpenAlex record. **The 57 low-confidence INs were held back**
for a person, on the local review page `citation-sweep/index.html` beside the
PDF library, which lists every verdict and its reason.

Three things the agents could not see across batches, fixed before writing:
the same method proposed under two names (the 2005 and 2006 multi-charge
papers), a paper appearing as two OpenAlex records, and 'ML conference' used
for RECOMB, CSB and LNCS proceedings, which are `peer-reviewed` here. It also
gave six families their second method, and so a page with no note; the
`INVARIANTS` guard in `check_counts.py` refused the commit until each had one.

**No code from the upstream Space is used.** It publishes no licence, so it is
all rights reserved and cannot be redistributed; `BioGeek/denovo-radar` is an
independent implementation, MIT licensed, crediting the original as the idea.

## Citation graph

`build_citations.py` is the offline builder: it walks every publication, queries Crossref (by DOI) and Semantic Scholar (by DOI or title search), resolves references back to local publication ids by DOI-exact or title match (see below), and inserts edges into `publication_citation`. Fuzzy matches are also logged to `citation_audit.csv` for human review. The script is intentionally NOT run by CI; it's ~30 min of network I/O and Semantic Scholar rate-limits hard. Re-run locally when new papers are added, eyeball the audit CSV, then commit the regenerated `denovo.db` + `denovo.sql`.

**`token_set_ratio` is the prefilter, never the verdict.** It ignores the
tokens one string has and the other lacks, so every reference containing the
four words of publication 315, "De novo Peptide Sequencing", scored 100
against it, and so did references to PEAKS, ContraNovo, AdaNovo and Depthcharge.
315 had collected 26 citations that way, and "Deep Learning in Proteomics" 7.
`titles_match()` now decides:

- a catalog title under five words matches only exactly;
- otherwise the title must appear as ONE contiguous span of the reference
  (`partial_ratio_alignment` ≥ 95) that STARTS a title: at the beginning,
  after punctuation, or after a capitalised word. "Deepnovov2: Better de novo |
  peptide sequencing with deep learning" contains publication 57's title after
  a lower-case word, so it is DeepNovoV2 and not 57;
- or, for a bare title, the two agree nearly in full (ratio ≥ 92) or one is
  the other plus a subtitle;
- and a match to a paper dated more than `YEAR_SLACK` (2) years after the
  year the reference prints is refused. Artemenko 2006, "De novo sequencing of
  peptides by mass spectrometry", scores 94 against a 2009 encyclopedia entry
  ending "using mass spectrometry", and no word-level rule separates that
  substitution from SPIDER's references, which say "containing" for "with".

The text is cleaned first, because what broke the first version of the rule
was markup and not wording: `&lt;em&gt;`, `\emph{De Novo}`, Crossref's
`\textbackslash emphłbrace`, ligatures (NFKC), a "Correction to:" prefix, and
a trailing page or line number on a bare title.

**A refuted edge is deleted; a missing one is not.** `publication_citation` is
INSERT OR IGNORE, so a wrong edge never left on its own. An edge goes when the
prefilter proposed it, the verdict rejected it, and nothing else this run
supports it; one that simply was not re-found, on a bad API day, stays. Every
rejection is written to the gitignored `citation_refused.csv`, with
`edge_deleted` marking the ones that removed an edge. The 2026-10-12 rebuild
took the graph from 6248 to 6863 edges: 678 added, nearly all papers catalogued
since the last full rebuild, and 63 deleted, each read against its reference.
61 were false. The other 2 are the price of the five-word floor: AdaNovo's
reference to PEAKS is printed as "Peaks: powerful software", and NovoBoard's to
AdaNovo as just "AdaNovo".

## Finding papers the catalog is missing

`build_candidates.py` answers "what should be in here that isn't", which the
database cannot answer by itself: `publication_citation` stores **only**
intra-catalog edges (0 of its rows point outside), so every reference to the
outside world is thrown away at build time. The script fetches the outside from
OpenAlex in both directions and scores each external work by **how many of our
publications link to it**, which is the signal that separates noise from a gap:
linked to one of our papers means nothing, linked to eight means something.

- **Backward** (works our papers cite) surfaces *foundational* gaps. The first
  run found "Fast algorithm for peptide sequencing by mass spectroscopy" (1990,
  29 of our papers cite it) and "PAAS 3: A computer program to determine
  probable sequence of peptides" (1984, 23).
- **Forward** (works citing our papers, scored by how many they cite) surfaces
  *new* work, and is the half worth re-running as the field moves.

Most high-scoring candidates are correctly out of scope: SEQUEST, Mascot,
MaxQuant, X!Tandem and target-decoy top the backward list because every de novo
paper cites a database-search baseline. That is expected, not a bug, and it is
why the script writes `candidates.csv` for review instead of inserting
anything. Apply the bar recorded in `WATCHLIST.md`: for a review, *de novo*
must be the subject; for an application paper, *de novo* must have produced part
of the result.

Already-rejected papers do not come back: the script excludes every DOI in
`WATCHLIST.md` and in `screening_decisions.tsv`, as well as everything already
in the catalog.

**`screening_decisions.tsv` is the bulk screenings' rejection list**, one DOI
per row, `rejected` or `held`, with the screener's reason and which screening
it came from (the 2026-10-04 citation sweep, the 2026-10-05 PRIDE search).
WATCHLIST.md argues about a paper in prose, which suits a dozen hand-made
calls and not 499. Without the file both tools re-proposed every sweep
rejection: `build_candidates.py` monthly, and the denovo-radar harvest, which
takes it as `--decisions`. `held` rows are excluded too, since they already
sit in a person's review queue; a held paper later added to the catalog is
simply catalogued, and its row can go.

## Finding code the catalog is missing

Two report-only proposers, and neither writes `algorithm_repository`:
`build_repository_candidates.py` reads the URLs a method's own PDF prints,
and `build_repository_search.py` asks GitHub (by method name and paper
title) and GitLab (by name) for the methods that still have none.

**A forge search is evidence of nothing until the README says so.** The
first full run examined 641 repositories for 333 methods. A name match
alone brings in a Flutter app called ReNovo, a Cleveland tech blog, forks,
and four releases of JUMPt, the Peng lab's protein-turnover tool, under
the JUMP sequencer's name. So a hit is `strong` only when its README cites
the describing paper (DOI, arXiv id or title) AND it is named after the
method or owned by an author. It is `probable` when it is named after the
method, in the domain and owned by an author. An author match needs the
surname AND the given name: with Yang, Li and Wang this common, a surname in
a login means nothing.

**Aggregators cite everything.** The trial run's two "strong" hits were an
Awesome list and a paper-digest blog, each citing hundreds of papers, so a
README citing five or more papers is discarded, as are forks.

14 repositories went in from the first run, after reading each: DpNovo,
PostNovo, MemNovo, PLMNovo, BiATNovo, AbNovoBench, DNML, CycloAnt, DBond,
Genomic Peptide Finder, the 2D-MS network code, the retention-time
extension, and RankNovo and OmniNovo as folders of `BEAM-Labs/denovo`.
`REJECTED` in the script records the hits turned down, with the reason for
each, so a re-run proposes only what is new.

## Mining the DNPS-DR feed

`build_dnps_candidates.py` reads the daily literature briefing at
<https://huggingface.co/spaces/yangtingpeng/DNPS-DR>, which is itself
publication 272, and reports which of its papers are not in the catalog. Same
contract as `build_candidates.py`: writes `dnps_candidates.csv`, inserts
nothing, touches no table. `refresh-dnps-candidates.yml` runs it monthly on the
3rd, the day after `refresh-candidates`.

The Space's repo carries `data/summaries.json`, a `{date: [{title, link, date,
summary}]}` dict of 1167 entries over 1030 dates, each `link` a PubMed URL. The
`summary` field is **ignored**: it holds the raw chain-of-thought of whatever
model wrote it, `<think>` blocks and all, in Chinese.

**TWO SENSES OF "DE NOVO", which is most of what the script is for.** The
feed's query is far broader than this catalog's scope. Of 1167 entries, 207 are
already catalogued -- reassuring, the feed does cover the field -- and of the
rest that say "de novo", most mean **de novo DESIGN**: RFdiffusion antibodies,
GLP-1 agonists, taste peptides, generative anything. A different field sharing
a Latin phrase. Others sequence a different analyte: glycans,
oligonucleotides, siRNA, DNA, transcriptomes. So a title must name sequencing
AND a peptide-ish analyte, and must miss both the design and wrong-analyte
vocabularies. **70 survive**, and every rejection is counted in the report
rather than dropped silently, because a filter nobody can see is a filter
nobody can correct.

**THE FEED IS STALE and a repo crawl cannot fix it.** Newest entry 2026-08-24,
file last committed 2026-08-27. The Space's own scheduler writes inside its
running container, so new days reach the git repo only when the author commits.
The script therefore surfaces the BACKLOG, which is large and worth having, and
will show nothing new until upstream commits again -- which is also why the
workflow is monthly rather than daily, and why an unchanged `lastModified`
costs one API call.

Two traps met while writing it. Europe PMC's `journalTitle` is **None** on
every `resultType=core` record; the venue lives at
`journalInfo.journal.title`, and reading the flat field wrote an empty column
for all 70. And resolving every entry's PMID to a DOI would be 1167 lookups,
so only the survivors are resolved -- but a DOI check against the catalog
afterwards still caught 4 papers the title match had missed.

**A candidate is a paper nobody has ruled on, and only that notifies.** The
builder used to check WATCHLIST.md by PMID and title alone, so a rejection
recorded anywhere else came back every month. The October list was one paper,
`10.1002/rcm.338`, which the first triage had marked "add, benchmark" and
`screening_decisions.tsv` had rejected two weeks later. Its abstract settles
it: Premstaller and Huber sequence 10- to 20-mer oligonucleotides, and the
title never says so, which is how it got past `OTHER_ANALYTE`. `decided()` now
drops every DOI in WATCHLIST.md and `screening_decisions.tsv`, so the list is
empty until the feed brings something new. That first triage lived in
`dnps_proposal.csv`, since removed: every paper it accepted is catalogued, and
both it rejected are recorded in those two files.

Something new then opens an issue, "DNPS-DR feed: papers to screen", assigned
to the repository owner, because a job summary is read only by someone already
looking. While that issue is open, a later run comments with just the
candidates it does not yet name, so an unanswered list does not re-notify every
month. Closing it is not the decision: cataloguing the paper, or recording its
rejection in one of those two files, is; otherwise the next run proposes it
again. A run that rebuilt the list and found nothing left closes the issue
itself; one that skipped because the Space was unchanged leaves it open, since
it checked nothing.

`refresh-candidates.yml` runs it monthly on the 2nd, a day after
`refresh-citation-graph`. It commits nothing and needs only read permission:
`candidates.csv` is gitignored like the other builder audit CSVs, so the
shortlist goes into the **job summary** (readable in the Actions UI without
downloading) and the full file is attached as an artifact. **It must never use
`.github/actions/commit-refreshed-db`**: that action recovers from a push race
by hard-resetting to `origin/main` and replaying the one table it owns, and
this job owns no table.
