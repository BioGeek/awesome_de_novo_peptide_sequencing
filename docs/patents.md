# Patents

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Patents

Five tables written by `build_patents.py`: `patent`, one row per patent
FAMILY; `patent_inventor`; `patent_algorithm`, the catalog method a patent
claims, with its evidence; `patent_citation`, what a patent cites that the
catalog holds; and `patent_cited_by`, every family, anyone's, that cites a
catalogued one. **64 patent families** from 47 assignees, 11 of them claiming a method
the catalog describes from its paper. Together they cite 45 catalogued papers between them.

**A patent is not a publication here**, and that is the design decision.
Typing it as a tenth `publication_type` would have reused every page and chart,
and it would have counted each patent as a paper in the wave, the author
rankings and both network charts. That is the inflation `'abstract'` was added
to avoid. Inventors are not authors, an assignee is not a byline affiliation,
and an examiner adds to a patent's citations, which no paper allows. So the
tables are separate and link OUT to the catalog: `patent_algorithm` to a method,
`patent_inventor.author_id` to a person, `patent.affiliation_id` to the
assignee's institution, `patent_citation` to a paper or another family.

**A row is a family, represented by one publication.** The DeepNovo patent is
also published as `US20190018019A1` and `WO2020014767A1`; one invention filed
in several countries is one row, with the others in `members`. The
representative is the granted US patent where one exists, else a US
application, else whatever a person chose. 0 publication numbers sit in two
patent rows, an invariant, and 0 patent rows have never been filled by a refresh.

**A family is Google's family id, and that id can be missing or too narrow.**
Russian `RU…C1` pages print none, and two IBMC patents then shared the family
"", so `add` refused the second as a member of the first; `parse()` now falls
back to the patent's own number. The opposite case needs `REJECTED`: one
invention filed in Canada or Hong Kong can carry a family id of its own, or none
(Rockefeller's `CA2406356A1` beside `US6271037B1`), and only a rejection naming
it as the same invention stops `search` proposing it. A shared priority date is
not proof of one invention: Micromass filed two on 1999-04-06, Skilling's MCMC
search (`US6489608B1`) and his Bayesian scoring (`US6489121B1`), and they are
two rows.

**Inventors printed in another script are pinned.** The name rules compare
Latin names, so the IBMC patents' Cyrillic inventors, ICT's and AMMS's Chinese
and Sogang's Hangul ones never match the PowerNovo, pNovo+, DiNovo and Han Bin
Oh rows they are. `INVENTOR_AUTHOR` pins them, each on a claimed method's
describing paper.

**Which patents belong is decided by hand**, on the catalog's own bar: the
claims are about *de novo* sequencing of peptides or proteins by mass
spectrometry, or about something done with its result. Reading the CLAIMS is
the test, not the title or the description. Verily's fragment-intensity and
retention-time patents and Tesorai's identification patent never say *de
novo* at all, and are out. UCSD's spectral-probability patent (`US8639447B2`)
is in, on dependent claims 3 and 4, which count *de novo* peptide
reconstructions, and is linked to MS-Dictionary, its group's *de novo*
hybrid. `REJECTED` in the builder records the near misses, so `search` does
not propose them again.

**An inventor becomes an author only on evidence.** A name that matches exactly
one catalog author is linked if that author wrote a paper describing a method
the patent claims, or is affiliated with the assignee. Otherwise the inventor
is left unlinked and the builder prints the near miss. `INVENTOR_AUTHOR` pins
what the rules cannot see, once a person has checked: Peter Cimermancic, a
Verily inventor, is the author of the 2011 peptidogenomics paper, which no
claimed method or shared institution could establish. His profile, read by
the owner, runs from Verily to Tesorai, where he is CTO.

**A patent's literature is matched by `build_citations.py`'s own matcher**
(`match_reference`, as a free-text citation), so prior art resolves to a
catalog paper by the same rules the citation graph uses. A cited paper must
also predate the patent's own publication: an examiner can add a paper
published after the priority date, but not one published after the patent. 0
patents cite a paper published after them, an invariant.

**Google Patents throttles hard, and the block outlasts the burst.** About
twenty page and search requests in two minutes earned HTTP 503 with a
"Sorry..." page, and it was still answering 503 more than twenty-five
minutes later. The builder paces requests 5 s apart (8 s for searches),
backs off 60, 300 and 900 s, never caches an error page, and caches every
good page and query in `.cache/patents/`. Two
syntax traps in the search endpoint: the `url=` parameter is the query string
URL-encoded TWICE, and fields are `TI=(...)`. The lower-case `ti:` prefix is
answered with a syntax error.

**An assignee is an institution, even with no catalog author.** A company
whose only link to the field is a patent gets an `affiliation` row, and so an
institution page listing its patents: Verily is the first. Its place is where
the patents were filed from, not today's headquarters (ROR has Verily in
Texas since 2023). Such a page says "1 patent family", not "0 authors and 0
papers", and points at the Patents section rather than at the map. A row is
added only once its patent is catalogued, because an institution page with
nothing on it would be a permanent URL for an empty page: Oxford GlycoSciences
and Venn Biosciences were held back until Google let their patents be fetched. `refresh` links a patent to its
institution itself when exactly one institution has the assignee's name once
both are folded by `org_base()`; a curated link is never replaced.

**Citing organisations get no page of their own.** 257 organisations cite
these patents, and most of them, from Visa to OPPO, have no other connection
to the field. A citer that already IS an institution here, by the same
one-match rule, links to its page from the chart (38 do), and that page lists
its citing filings under **Its patents citing catalogued ones**: a link from
the chart to a page that never mentions patents is a dead end, which Protein
Metrics' was. Two institutions sharing a folded name link neither.

**Every patent family has a page**, `pages/patents/<number>.html`, and the
slug is the representative publication number VERBATIM, upper case, not
slugified: a patent number is an identifier people type. Every link on the
site leads there first, with Google Patents one ↗ away, the way a paper's
title leads to its page with the DOI beside it. It shows what Google's page
cannot: the method claimed, inventors linked to their author pages (by the
catalog's name for them, not the register's "Ngoc Hieu TRAN"), the
catalogued papers and patents it cites, the catalogued patents citing it, and
every citing family grouped by organisation, linked where the organisation is
an institution here. The assignee is shown by the institution's name, with
the printed form beside it only where it differs in more than case and
punctuation. Its JSON-LD is a `CreativeWork`, because schema.org has no
Patent type. If a family's representative changes, say an application is
granted, the URL changes with it and `slugs.py --check` fails like any
rename, wanting a `REDIRECTS` entry. Elsewhere a patent appears wherever it
meets a page: the method it claims, an inventor who is a catalog author, the
assignee's institution, and every catalogued paper it cites, under **Cited in
patents**.

**The home page's Patents section** has the prior-art chart and a table. The
chart runs time DOWN the page: each patent in the right-hand column at its
PRIORITY date, which is what prior art is judged against, and the papers it
cites in the left-hand column, at their publication dates. A solid line is a
patent citing the paper of the method it claims; an arc beside the patents is
one catalogued family citing another. It ran across the page until 64 families
stacked its labels 29 tiers high; vertical, every patent label has a line of
its own, placed by a 1-D dodge.

**"Who cites these patents" is the other direction**: a dot for each
organisation and catalogued patent it cites, coloured company, academic or
hospital, hollow where an assignee cites its own earlier patent. Google lists
citing FAMILIES once each, plus citing publications; a direct citer usually
reappears among the families under another member's number, so the family
list is used whenever there is one. **An assignee is printed in its filing's
language**, so PrognomIQ arrived as "PrognomIQ, Inc.", "Prognomiq Inc" and
"プログノミック インコーポレイテッド". `organisation()` folds legal suffixes,
holder wrappers ("The Governing Council of...") and case, keeping the
spelling with the most capitals that is not all capitals, and `ASSIGNEE_EN`
holds the English name of every Chinese, Japanese and Korean assignee met so
far. Names are settled over the whole table rather than row by row, or one
company's rows would carry whichever spelling each met first. **A computed
sector is a keyword match, and "International" contains "national"**: that
put Honeywell and Visa in academia until the keyword got its word boundaries.
**A legal suffix is stripped only at the END of a name**, and never from
"& Co.": the first rule matched "Co" anywhere and turned "Merck & Co., Inc."
into "Merck &".

**`refresh-patent-candidates.yml` runs the search monthly** (the 4th, 10:00
UTC) and opens or updates the issue "Patents: families to screen", the same
loop as the DNPS-DR feed. It commits nothing. `--fresh` re-asks every query
and refetches the catalogued patents' own pages, since a new filing citing one
of ours is the commonest way a new patent appears; candidates' pages come from
a cache carried between runs with `actions/cache`. **A runner's share of
Google is about eight requests**: the first run got eight queries through and
was refused everything after, and at six minutes of backoff per refused
request it would have hit the job's timeout before the issue step. So the
first request refused after its retries stops all asking for the run, and CI
spends a budget of 20 requests in a fixed order: queries, then the catalogued
patents' pages oldest first, then candidate pages. A refused query falls back
on last month's cached answer. Only an unanswered QUERY makes a run
incomplete: it leaves `patent_candidates.incomplete` and never closes the
issue, while a page not refreshed only means older citations. A family is
matched against the issue by any member's number, because the same family can
come back under another one.
