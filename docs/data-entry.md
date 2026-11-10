# Entering publications: dates, preprint versions, abstracts, affiliations

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Publication dates

`publication.publication_date` is a full `YYYY-MM-DD` string with no NULLs, so a
date must always be produced even when the source has coarser precision. Two
conventions, both reverse-engineered from the existing rows rather than written
down anywhere, and worth following so the timeline stays comparable:

- **Which date.** For a journal with a print issue, use the **issue** date, not
  the online-first date. Of the sampled rows where Crossref's `published-online`
  and `published-print` disagree, 6 of 7 carry the print date. For an online-only
  journal (BMC, PLOS, Frontiers) the online date *is* the publication date, and
  any nominal "issue" it is later bundled into can postdate the article by
  months: Proteome Science 8:24 went online 2010-05-10 but sits in a Dec 2010
  issue.
- **Coarser precision.** Month-only sources get `YYYY-MM-01`; 437 rows use day
  `01` and 388 of those are in non-January months, so a first-of-the-month date is
  normal here and not a red flag by itself.

**The trap:** OpenAlex reports `publication_date` as `YYYY-01-01` whenever it
holds only year precision. Copied in as-is that is indistinguishable from a real
1 January, and it silently backdates a paper by up to a year: Chemical Science
16(39) read as 2025-01-01 when it was published 2025-09-04. Four such rows were
corrected (66, 153, 169, 171, plus thesis 57 from UWSpace's own metadata) by
going to the publisher, Europe PMC or the repository instead. No builder writes
`publication_date`, so these stay fixed, but hand-entry from an OpenAlex record
can reintroduce it. When a source gives only a year, prefer the publisher page,
Europe PMC `firstPublicationDate`, or a repository's `citation_publication_date`
before settling for `YYYY-01-01`.

**The other trap is a thesis date on the journal row.** Publication 50,
NovoRank, was dated 2022-08-01 while its DOI is `10.1021/acs.jproteome.4c00300`
-- an ACS `4c` identifier, which means 2024. Crossref gives J Proteome Res
24(2), 903-910, online 2024-12-31, print issue 2025-02-07. The 2022 date was
Jangho Seo's master's thesis of the same work, which is now publication 359 in
its own right, and the row was carrying its author's thesis date for two and a
half years of the timeline.

**The ACS DOI suffix is the tell**: `4c` is 2024, `5c` is 2025, the same way an
arXiv `25xx` id dates a posting. When a row's date and its DOI disagree by more
than a few months, the date is usually a different publication's. Note the
method itself keeps its 2022 first appearance, because `first_pub` takes the
earliest DESCRIBING paper and the thesis is now that paper -- which is the
honest reading, and a reason to enter the thesis rather than just fix the date.

**A year-only date can come from Crossref too, and SSRN is where it bites.**
The OpenAlex trap above has a Crossref twin: for publication 432, LIPNovo+ on
`10.2139/ssrn.6890054`, Crossref's `posted` and `issued` both read `[[2026]]`,
year precision and nothing more, which copied in becomes 2026-01-01. The
documented fallbacks do not help here either, because SSRN has no Europe PMC
record and its own abstract page answers **HTTP 403** behind Cloudflare, so
there is no publisher page to read a real date off. What Crossref does carry is
`created`, the DOI registration timestamp, 2026-06-06, which for an SSRN
posting is the deposit that accompanies it. That is the date the row holds, and
it beats 1 January by five months of timeline. **Prefer `created` over a
year-only `posted` whenever the publisher page cannot be read**, and note it is
an upper bound on the posting rather than the posting itself.

**SSRN's author metadata can be wrong where its PDF is right.** For
publication 633, PeposX-Exhaust on `10.2139/ssrn.4627176`, the landing page
and Crossref both list "Liu Yang (Hunan Agricultural University)" sixth and
"Yang Liu (Wuyi University)" seventh. The posted PDF prints "Yang Liu" with
Wuyi and Hunan affiliations sixth and Lei Liu seventh, exactly as the
journal version does: the submitter reversed one name and mistyped another.
The byline follows the PDF. An author row made from such a record ("Liu
Yang", 2370) is a duplicate, and was folded into the real one.

Five rows legitimately keep 1 January (146, 158, 187, 286 and 310: Mass Spectrometry
Reviews 34(1), Mol Cell Proteomics 8(1), AIChE Journal 53(1), J Biol Chem 279(1),
Biomedical Chemistry: Research and Methods 1(1)) because each really is a
January issue. Two theses keep a year-only 1 January because their repositories publish no
month: publication 196, whose Digital Commons ETD record gives "Date of Award
2013", and publication 339, whose figshare-backed record at Victoria University
of Wellington gives "Date of Award = 2020-01-01" as a literal placeholder. Note
that record's `published_date` is 2023-09-26, which is when the repository
deposited the thesis, not when it was awarded.

## Preprint versions

Normally one `publication` row per preprint, with `url` pinned to the version
whose metadata the row reflects (InstaNovo's points at `v3`). BiATNovo is the
exception and the worked example: bioRxiv v1 and v2 differ in BOTH title and
author list (5 authors, one of whom is dropped, versus 10), so each version gets
its own row and its own `publication_author` byline. `publication.version`
distinguishes them, the same column that separates Casanovo v1 from v2.

Two traps if you ever do this again:

- **The DOI must go on the EARLIER row.** bioRxiv mints one DOI for all versions
  (the versioned form `10.1101/...540352v1` is a Crossref 404) and
  `publication.doi` is UNIQUE, so only one row can hold it. Citation harvesting
  resolves the DOI to whichever row holds it, and papers cite a preprint from
  before a later revision exists. With the DOI on the later row,
  `build_citations.py`'s `is_valid_citation()` discards those edges as citations
  of a future paper, silently and with no audit entry, on every monthly rebuild.
  Anchored on the earliest row every edge stays valid. The cost is that the
  newer row shows no DOI and no OpenAlex count, which is the right place for
  them anyway: both describe the preprint as a whole.
- **Do NOT link the versions through `publication_version`.** That table drives
  the Publication-lifecycle chart's preprint-to-journal gap; a v1-to-v2 pair
  would render as a preprint that took 17 months to be "published" when neither
  version is. The versions are already linked by sharing an `algorithm` row plus
  distinct `version` labels, which is how the architectures swim lane groups
  them.

Note this makes `n_preprints` count both versions, exactly as it already counts
Casanovo's several preprints.

## Abstracts

`build_abstracts.py` fills `publication.abstract`, trying bioRxiv, arXiv,
**Europe PMC**, OpenAlex and Crossref in that order and recording which one won
in `publication.abstract_source`. A NULL `abstract_source` alongside a non-empty
`abstract` means the text was entered by hand and is authoritative: the script
skips those rows unless `--force`, so don't pass `--force` casually.

Coverage is 933/1001, of which 5 came from the PDFs themselves via
`build_pdf_abstracts.py` (`abstract_source = 'pdf'`). Four more carry
`abstract_source = 'proceedings'`: the ICML and NeurIPS rows 438 to 441, whose
abstracts were copied from the proceedings page itself.
The 68 without one are mostly theses, conference pages and records with no DOI,
where neither an API nor the PDF yields a clean abstract.

**`build_pdf_abstracts.py` rejects more than it accepts, 13 of 18, and the
rejections are the point.** Lifting an abstract out of a PDF fails in ways that
look like success:

- publication 254's two-column layout puts "Abstract" and "1. Introduction" on
  the SAME physical line, so a heading search lands in the wrong column and
  returns the introduction, which reads perfectly and carries five citations;
- publication 20 ran past the abstract into Chinese margin annotations;
- 55 stopped mid-word at "backed by high-", 258 carried on into the
  introduction, 318 finished on a page number ("a special de ii");
- `-layout` leaves a hyphen plus a SPACE where it joins two columns, giving
  "Data-Independent Acquisi- tion" and "the pep- tides", and ICLR margin line
  numbers survive inside the line as zero-padded triples.

The first guards -- starts with a capital, 200 to 4000 characters -- passed all
four of those. What catches them is checking the END: a real abstract finishes
on a sentence terminator. With that, plus no CJK, fewer than three inline
citations, no Introduction heading and no surviving line numbers, 11 apparent
successes came down to 5 correct ones. Loosening any of them puts a
plausible-looking fragment in the database, which the rule above already says
is worse than no abstract at all.

Europe PMC is asked before OpenAlex on purpose. OpenAlex reassembles an
inverted index that, for Nature-family journals, has the journal's separate
one-sentence editorial summary glued onto the abstract with no marker to cut
on: Nat Commun 15 on `10.1038/s41467-024-53105-8` returns 1470 characters
against a real abstract of 1151, the extra ending "Here the authors
present...". Europe PMC serves the abstract alone. It does answer 200 with an
empty `resultList` rather than 429 when pushed, which is indistinguishable from
"no record", so `from_europepmc` retries once.

`strip_publisher_extras()` removes the rest: Liebert and SAGE journals keep a
promotional `Teaser:` field that Crossref AND Europe PMC both concatenate onto
the abstract (Astrobiology 23:657 arrives 291 characters too long), plus two
format artifacts, a space left inside a bracket by tag stripping ("( e.g.,")
and trademark symbols. Between them the script now reproduces both
hand-corrected abstracts byte for byte, and alters none of the other 240.

Two guards worth knowing about, because both were hit in practice:

- OpenAlex delivers abstracts as an inverted index (word -> positions) that has
  to be reassembled in position order, and some records carry only a PARTIAL
  index. That reassembles into a fragment starting mid-sentence, so any
  candidate whose first character is lowercase is rejected and the next source
  is tried. A fragment presented as an abstract is worse than no abstract.
- arXiv DOIs are minted as `10.48550/arXiv.2512.12272`, but the API's `id_list`
  wants the bare `2512.12272`. Leaving the prefix on returns an empty feed
  rather than an error, which silently falls through to OpenAlex.

## Affiliations, and why the ROR matters

`build_affiliations.py` was the catalog's last unautomated entity. Until it
existed, `affiliation`, `author_affiliation`, `city` and `country` were the only
entity tables with **no writer at all**: every institution, department, city and
country was typed in by hand from a byline. Nine of sixteen tables had no
builder; this closes the largest gap.

The data was already being fetched and discarded. `build_author_ids.py:135`
loops `authorships[]` reading only the author fields, and drops
`institutions[]` and `raw_affiliation_strings` on the same matched authorship.

**Identity is the ROR, not the name.** The apparent disagreement between
OpenAlex and the curated rows is two facts in one column:

| column | meaning | shown on the site |
|---|---|---|
| `affiliation.name` | as PRINTED on the paper | yes |
| `affiliation.canonical_name` | current legal name (OpenAlex) | no |
| `affiliation.ror` | identity | no |

"Swiss Federal Institute of Technology" is what a 1999 byline says; "Ecole
Polytechnique Federale de Lausanne" is what EPFL is called now. Keyed on the
ROR they stop competing and the site keeps saying what the paper said. Note
`ix_affiliation_ror` is deliberately **NOT unique**: a ROR identifies an
institution, but this table's grain is (institution, department), and Technical
University of Munich alone has ten rows.

**Resolution is tiered, and the tiers are not equally trusted.** Tier 1 matches
a known ROR, tier 2 an exact normalised name, tier 3 our as-printed name
appearing verbatim in the paper's own affiliation string. Tier 3 establishes
which of our rows a byline belongs to, but **must never write a ROR**: one raw
string routinely names a company and a funding body, so trusting it bound
"Baizhen Biotechnologies Inc." to the Wuhan Science and Technology Bureau's ROR
and "Talus Bioscience" to TRIA Bioscience's. That was caught and reverted; the
guard is `if ror and tier in ("ror", "name")`.

**Departments are parsed, so they are never auto-written.** OpenAlex has no
structured department field. Because `affiliation` is UNIQUE(name, department),
a wrong department does not mislabel a row, it creates a duplicate institution
and pollutes both the institution page set and the geo chart. Candidates go to
`affiliation_audit.csv` for a human.

**Per-byline, not per-author.** `author_affiliation` is author-level, so joining
`publication_author -> author_affiliation` returns every institution an author
was ever given rather than the one on that byline, which makes any per-paper
geography claim wrong for the authors who have several. The new
`publication_author_affiliation` table records the precise relation; the
curated author-level rows are left untouched.

Results of the first full run: 1922 per-byline rows, 427 of 621 affiliations
given a ROR, and city coordinates filled from `/institutions/{ror}` geo, taking
the cities missing lat/lng from 106 down to 27. 818 findings went to the audit
CSV, of which the largest group is institutions OpenAlex models as separate
entities where this catalog records them as departments of a parent (Novo
Nordisk Foundation under DTU, Science for Life Laboratory under KTH, Academy of
Mathematics and Systems Science under CAS). Those are correct refusals, not
failures.
