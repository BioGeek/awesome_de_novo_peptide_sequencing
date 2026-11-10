# The local PDF library

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## The local PDF library

`build_pdf_library.py` maintains a folder of paper PDFs beside the catalog:
`report` says what is missing and why, `fetch` downloads what a source states
is free, `ingest` files hand-downloaded PDFs, `rename` re-derives every
filename from the catalog and `dedupe` drops byte-identical copies. It writes
`pdf_status.csv` and `missing/*.txt` into the LIBRARY, never into the repo, and
touches no table.

**ADDING A PAPER INCLUDES FETCHING ITS PDF.** After the insert, run

    python3 scripts/build_pdf_library.py fetch --ids <the new publication id>

which writes the file straight into the library root, Zotero-named. When
nothing free can be fetched, run `report`: the paper lands in whichever
`missing/*.txt` describes why, and THAT is the expected outcome rather than a
failure. The two lists to read it in are `blocked-but-open-in-pmc.txt`, meaning
a free copy exists and only a browser can take it, and `paywalled.txt`, meaning
no source reports one at all.

**The PDFs also feed `~/code/denovo_wiki`**, an LLM-compiled, cited knowledge
base built from them (local only, never published). The `post-commit` and
`post-merge` hooks call `.githooks/denovo-wiki-queue`, which, when `denovo.db`
or `denovo.sql` changed, records the new publication ids in
`~/.local/state/denovo_wiki/pending.tsv` and prints a one-line reminder. It runs
no LLM work, never fails a commit and is silent when the wiki clone is absent.
Do not ingest from this repo: the owner runs `/denovo-wiki sync` in
`denovo_wiki` when they choose. A paper whose PDF is fetched after its commit is
picked up by that sync once the file is in the library.

**Filenames carry the FULL title**, in the catalog's own casing, with nothing
cut. The old `[:95]` came from what Zotero happens to export and cost
information on 154 of 359 papers; the longest full name this catalog can
produce is 223 bytes, well inside the 255-byte `NAME_MAX`, so the length guard
in `zotero_name()` is for a hypothetical future title and fires on nothing
here. Renaming the library to full titles touched 103 of 231 files.

**A twin pair gets a `(preprint)` suffix.** A preprint and its version of
record with the same normalised title, year and first author produce the same
filename, and that cost a real file: fetching LIPNovo's ICML PDF overwrote the
arXiv one already in the library. So `load_publications` flags such pairs as
`twin`, `zotero_name` appends ` (preprint)` (or ` (postprint)`) to that side,
`identify` honours the marker, and `fetch` never overwrites an existing name.
**An unmarked file is not proof of the version of record**: a preprint filed
before its twin existed carries no marker, and the first version of this rule
proposed renaming four bioRxiv and ChemRxiv files onto their journal twins'
names. `identify` now reads the first two pages for a preprint banner
(bioRxiv's, ChemRxiv's, arXiv's `arXiv:2602.20209v1 [q-bio.QM]` margin stamp,
which unlike a bare arXiv id cannot come from a reference list).

That re-attributed two papers' mined tables. GA-Novo's and the
regressor-guided diffusion paper's comparison tables had been read from their
arXiv files while carrying the journal rows' ids (152, 7); neither journal row
has a PDF of its own, so the 11 sign-offs were re-keyed to the preprint rows,
224 and 222, along with GA-Novo's registry entries.

**A short title is checked against the file.** A filename names a paper, and
the owner's Zotero export named one wrongly: `Ma - 2016 - De novo Peptide
Sequencing.pdf`, filed under Bin Ma's chapter (publication 315), was a second
copy of the InstaNovo preprint (publication 1), whose own title contains those
four words. The accession miner had read it as 315 and given that 2016 chapter
nine dataset links, ProteomeTools and the InstaNovo deposits among them. All nine
are deleted, and so is the file; the chapter is paywalled. When the title a
filename matches is under `SHORT_TITLE_WORDS` (6) words, `identify` now reads
the first two pages and, if a longer catalog title is printed there, files the
PDF as that paper. Over the whole library this moves this one file and no other.

**A version of record is never filed with a preprint's PDF.** OpenAlex can
fold a journal article and its preprint into one work (38 pairs here share
an OpenAlex id), and then offers the preprint server's PDF for the article.
Publication 997, the Nature Methods sequence-ambiguity benchmark, was filed
with its bioRxiv preprint that way, and a dataset check read the preprint
twice believing it had read both. `fetch` now refuses, for any row that is
not a preprint or postprint, a PDF whose first two pages carry a
`PREPRINT_BANNER`, and moves on to the next candidate: for 997 that is
Nature's own `citation_pdf_url`.

**Two tie-breaks, and the first one was wrong.** Where two rows differ only in
a title's capitalisation or punctuation -- publication 108's "NovoBoard: a
comprehensive framework" against 80's "A Comprehensive Framework", 29's
"data-independent" against 103's "data independent" -- `title_score`
normalises the difference away and the pick is arbitrary. The first fix
preferred "whichever candidate the current filename already matches", which
tied the answer to the name: dropping the truncation changed every name, and
four files started matching the other row of their pair, so `rename` proposed
lateral moves it could never apply because the target was taken. It now breaks
the tie on the EXACT characters of the title against the filename's title text,
then on the lower id -- a rule that reads only the title and so converges
wherever it starts. Verified: `rename` reports 0 on the second pass.

**A row pinned to a bioRxiv version is fetched as that version and no
other.** A bioRxiv DOI is shared by every version, and `candidates()` used
to try the newest version first, then OpenAlex's locations, which name
whichever version OpenAlex saw. The Kaiko preprint's two rows came back
swapped: v1 (994, which holds the DOI) got v2's PDF, and v2 (995, no DOI)
got v1's through OpenAlex. Each file carried the other's name, and only the
"this version posted" stamp on page one showed it. Now a `...vN` URL makes
that version's PDF the only candidate, with no fallback that could file
another version under the row's name.

**`dedupe` checks the publication as well as the hash.** Byte equality is too
strict for the same paper twice: bioRxiv served one preprint to two fetches 26
bytes apart, a timestamp inside the PDF, so two copies each of Sanders 2024 and
pi-PrimeNovo 2024 sat in the library with different hashes while `rename` could
only report a rename it could never apply. Files are now also grouped by the
publication they resolve to, and that group is REPORTED rather than deleted: a
preprint and its version of record legitimately give one paper two files under
two rows, and choosing between two copies of one row is a human's call.

**The library lives at `~/Documents/de_novo_peptide_sequencing`, its PDFs in
`pdfs/`.** The root holds only the working folders beside them (`manual/`,
`missing/`, `supplements/`, `comparison-review/`, `citation-sweep/`) and
`pdf_status.csv`. Every script takes the root from
`build_pdf_library.DEFAULT_DIR` and finds PDFs through `pdfs()`, which still
reads a PDF left at the root or in `retrieved/`, and every writer goes through
`pdf_dir()`. Three scripts used to hard-code the old path with spaces in it.

**The library is ONE flat folder.** There used to be a `retrieved/`
subdirectory separating downloads from the owner's own Zotero exports. Once the
same generator named every file and `pdf_status.csv` recorded the provenance,
the split only hid half the library from `rename` and `dedupe`, each of which
looked in one folder and not the other. `pdfs()` still reads `retrieved/` if it
exists, so an older layout keeps working, but nothing is written there.

**It must never run in CI.** There is no library on a runner, and a full
`fetch` is a few hundred HTTP requests. `pypdf`, needed only for slicing, is
deliberately not a project dependency for the same reason; the command line
above passes it with `uv run --with`.

Four things in it were learned the hard way and are worth not re-learning:

- **Fetch through `curl`, not `requests`.** HTTPS on the machine this was
  written for is intercepted by a Perimeter81 Secure Web Gateway whose
  re-signed certificates carry no Authority Key Identifier, so Python rejects
  them with `[SSL: CERTIFICATE_VERIFY_FAILED] Missing Authority Key
  Identifier` while `curl`, against the *same* CA bundle, returns 200. The
  first run read that as the publishers blocking us: 36 downloads of 322, with
  140 filed under "open access but the download failed", including five theses
  hosted on this project's own domain. Switching transport took it to 101. If
  everything fails at once, suspect the transport.
- **Match a filename to a title prefix-against-prefix, cut to the shorter
  string.** A filename is a truncated title, and both obvious metrics are
  wrong in opposite directions: `partial_ratio` scores a short title sitting
  inside a long filename at 100, which filed DeepNovoV2's paper under
  publication 57 ("Peptide Sequencing with Deep Learning"), and
  `token_set_ratio` ignores unmatched tokens, so publication 315 ("De novo
  Peptide Sequencing", two words) scored 100 against 26 of 37 files. Where a
  preprint and its version of record share a title, the filename's year breaks
  the tie; `--map` handles the rest.
- **Take the first author from an ordered subquery**, never from
  `GROUP_CONCAT` with a trailing `ORDER BY` -- see the byline note under **The
  Quarto site**. It named 52 of 107 downloads after the wrong author.
- **Slice with `pypdf`, not `pdfseparate` + `pdfunite`.** The latter copies the
  volume's shared fonts onto every page: a 9-page slice of a 31 MB proceedings
  volume came out at 32.7 MB, against 0.3 MB from `pypdf` with
  `compress_identical_objects`.

**Only locations a source says are free are ever fetched**, and a paper with
nothing free is reported rather than worked around. PMC is not tried at all:
every automated route into it is shut by design, and it directs bulk users to
its FTP/cloud packages instead. `report` therefore lists a paper with an
open-access PMC copy as a **PMC link**, in `blocked-but-open-in-pmc.txt`,
because that link opens normally in a browser and is one click from the PDF.

**A browser User-Agent changes nothing here, measured.** `--browser-ua` sends
one, for open-access papers whose publisher refuses the default agent; it is
opt-in because misrepresenting the client is a choice worth making knowingly,
and it gets past bot detection rather than any paywall. Run over the 27
publisher-blocked papers it recovered **0**: ACS, Europe PMC's `?pdf=render`
and IEEE answered 403, 403 and 202 exactly as before, so the block is not the
agent. What that run did expose is the next entry.

**Europe PMC reports availability; OpenAlex reports that a deposit exists.**
Where they disagree, Europe PMC wins, and publication 360 is why. OpenAlex says
`is_oa` true, `oa_status` green, `oa_url` PMC13457817 -- and that PMC record is
**embargoed until 2027-08-10**, which OpenAlex's green status does not
distinguish from readable. Europe PMC says `isOpenAccess` N, `inPMC` N, `hasPDF`
N, no `pmcid`, "Subscription required": correct on every count. Of 27 papers
the report called "open access but blocked", **22** were that shape, free on
OpenAlex's word alone. So `epmc_verdict` overrides, and OpenAlex only speaks
where Europe PMC has no record.

Two incidental traps in the same place. OpenAlex puts `is_oa` under
`open_access`, not at the top level, so `work["is_oa"]` reads None for every
work. And a PMC id is NOT a readable copy: `pmcid()` used to grep `PMC\d{4,}`
out of the raw cached JSON, which put 360's embargoed deposit on the
"open in PMC" list a year before it opens, so it now requires Europe PMC to say
the text is actually there.

**The Europe PMC query must be URL-encoded**, and this is the worst bug this
script has had. The query needs literal double quotes around the DOI, and an
unencoded `"` makes Europe PMC answer **HTTP 400**. The first version passed
params through `requests`, which encoded them; switching the transport to
`curl` moved the URL into an f-string and broke every Europe PMC lookup from
then on. Silently: a failure is not cached, so it retried and failed again, and
an HTTP 400 was indistinguishable from "this paper has no record" -- the script
carried on deciding access from OpenAlex alone. The answers already on disk
predate the switch, which is why it surfaced only on a newly added paper.
`get_json` now PRINTS a failed lookup rather than returning something that
reads like an empty result.

**Read the PMC id out of the raw cached JSON, not from one field.** The first
`pmcid()` asked Europe PMC for `pmcid` and required `isOpenAccess == "Y"`, and
so missed publications 86, 91, 95 and 120 -- all four of which Europe PMC had
already offered an "Open access" PDF location for, with the PMC id sitting in
the URL. They were filed as publisher-blocked, the hardest bucket, when a PMC
link opens fine. Fixing it moved 24 papers from `blocked-publisher` (27 down to
2) into `blocked-but-open-in-pmc` (12 up to 36).

**An EMBARGOED paper is neither paywalled nor blocked, and saying so stops it
being chased.** `EMBARGOED` in the builder maps a publication id to a release
date and the publisher's own wording; `fetch` skips those ids and `report`
files them in `embargoed.txt` before any stored verdict is read, since an
earlier run will have recorded "paywalled", which is the wrong word when the
text is not for sale either. Recording the DATE rather than a flag means the
entry expires by itself: once it passes, the paper rejoins the normal buckets.

One entry so far, publication 119, the DiffNovo-DIA thesis. UNT's record says
"The contents of this dissertation are unavailable for full viewing on this
site ... It will be made available on this site on June 1, 2030", and the DOI
it offers as an alternative resolves back to the same embargoed record, so
there is no second route. This is the same shape as publication 360's
PMC deposit, embargoed to 2027-08-10, which the Europe-PMC-wins rule above
already handles because Europe PMC reports it; a repository embargo has no
such API to ask, so it is recorded here by hand.

**A paper a person has checked stays checked.** `CHECKED` in the builder
maps a publication id to a verdict, `no-pdf` (no full text exists or ever
will: this catalog's own Zenodo code deposit, the two abstract-only JSSR
records) or `paywalled` (sold, and no free copy found by hand). `fetch` skips
them and `report` files them before any stored verdict, `no-pdf` in its own
`no-pdf-exists.txt`, so the missing lists shrink to the work actually left
instead of re-proposing what the owner already looked at. The first hand
check also paid for itself the other way: publication 700, listed as
paywalled at IEEE, has the authors' own copy on HAL (`inria-00270867`), and
is filed rather than recorded. Check an open repository before writing a
paper off.

**Do not bucket on the last host tried.** Doing that produced a
`blocked-doi-resolver` list of 31 which the README then recommended as the
largest recoverable group, on the theory that OpenAlex had offered `doi.org` as
their PDF location. It had not: `doi.org` was simply the record's own `url`,
tried LAST after the real open-access candidates failed. Following the DOI and
reading the landing page's `citation_pdf_url` recovered **0 of 31** -- ACS, OUP
and MDPI answer 403 at the landing page itself, and Wiley and Elsevier's
linkinghub answer 200 with no such tag. The bucket now keys on whether an
open-access copy is known to exist, which is a fact about the paper rather than
an artefact of the attempt order.
