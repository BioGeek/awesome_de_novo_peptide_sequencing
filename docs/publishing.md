# Publishing: render scope, structured data, IndexNow and the URL lock

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## The publish renders what changed, not everything

`render_scope.py` decides, before Quarto is invoked, whether the publish needs
**full**, **partial** or **index** scope. A full render is 1017 seconds and
98.7% of the job, essentially all of it the ~2485 generated pages at ~0.4s each;
index.qmd alone is 13.5s.

| mode | when | what runs |
|---|---|---|
| `index` | no generated page changed | `quarto render index.qmd` |
| `partial` | 1 to 100 pages changed | index.qmd, then one `quarto render` per changed page |
| `full` | Quarto version, `_quarto.yml` or `custom.scss` changed; no usable `_site`; more than 100 pages changed; or the workflow's `full_render` input | `quarto render` |

`index` is the common case: build_pages.py keeps volatile metrics out of the
pages, so the daily repo-metrics commit changes zero of them. `partial` is the
case this section is about, and the two that produce it are a new paper and the
weekly benchmark refresh, which touches the 17 method pages carrying a benchmark
line and nothing else. Authors, publications, institutions and venues are not
affected by a benchmark number, so they are not re-rendered.

**`_site/.render-manifest`** is how one run tells the next what it built: the
Quarto version plus `_quarto.yml` and `custom.scss` in one "global" hash, and
then a hash per page keyed by path. Path as well as content, so a slug rename
registers as a removal and an addition rather than as nothing. It lives inside
`_site` because gh-pages carries it to the next run, the same trick that makes
the restore step a free and exact cache. It replaced `.render-key`, a single
hash over everything, which could only answer "did anything change".

**One `quarto render` per file, because `quarto render a.qmd b.qmd` silently
renders only the first input.** That is the kind of thing that ships as "the
partial render works" while quietly publishing stale pages, so it is worth
saying twice. The per-file cost is ~6s in CI against ~0.4s inside a project
render, which is what sets the `MAX_PARTIAL = 100` cap: past that the full
render is both faster and more predictable.

**All three modes have now run in CI**, which is where the per-file number above
comes from:

| mode | run | wall clock | what it rendered |
|---|---|---|---|
| `full` | 36714122487 | 17m41s | everything, 2521 pages |
| `partial` | 36716139226 | 3m48s | index.qmd + 24 family pages, 192s in the render step |
| `index` | 36709905609 | 1m07s | index.qmd, 2497 pages byte-identical |

192s for index.qmd plus 24 pages puts a page at roughly 6s once index.qmd's own
~40s is taken out, so the 100-file cap costs about 10 minutes against 17m41s for
the full render. Break-even is nearer 160 files; the cap stays at 100 because a
change that size is rare and the margin is worth more than the minutes.

**The full render grows with the catalog, and the job's timeout has to grow
with it.** At about 0.4 s per page the 17 minutes above was for 2521 pages;
the citation sweep took the site to 6282, about 42 minutes, and the job's old
`timeout-minutes: 30` cancelled run 37236776725 at page 4370, leaving the
live site on the previous publish. It is 90 now. Re-check it whenever a bulk
import adds pages: the slug count `slugs.py --check` prints is the number to
multiply by 0.4 s.

A **full** project render empties `_site` and repopulates it at the end, so the
directory is bare for the whole 17 minutes; a partial render writes into
whatever is already there. The restore step therefore matters only to the two
fast paths, and the manifest is recreated after every render rather than carried
through one.

**What a partial render leaves alone**, all verified rather than assumed:

- the other pages in `_site`, which keep their bytes;
- `sitemap.xml`, which Quarto MERGES into rather than rewrites: it keeps all
  2449 entries and refreshes the ones it rendered. It only merges when the file
  is already there, though. Delete it and an index-only render produces a
  sitemap with **one** URL, which is the second reason the workflow restores
  `_site` from gh-pages before rendering anything;
- `search.json`, which `postrender.py` rewrites after EVERY render with one
  compact record per generated page. Quarto itself never indexes the pages
  (`search: false` in their metadata), because it would index each page's full
  text and every visitor downloads the file before their first keystroke. The
  record holds the page's title, its type ("Author", "Method", "Dataset"...)
  and the one-line description written for its meta tag, plus a dataset's
  accessions, so typing a name or `PXD003868` in the navbar search finds the
  page: 7186 records when measured, 2.7 MB, about 0.5 MB compressed. Records under `pages/`
  are replaced on each run, so a partial render cannot leave stale ones.
  Verified in headless Chrome: "Pevzner", "Casanovo", "Karolinska",
  "nine-species" and "PXD003868" each find their pages.

**A removed page forces the full render**, and the reason is the sitemap rather
than the page. Since Quarto merges into an existing `sitemap.xml`, neither fast
path can drop the entry, and the restore brings both the entry and the orphaned
`.html` back on the next run; only a full render, which empties `_site` and
writes the sitemap from what it produced, forgets a page. Removals are rare and
deliberate -- `slugs.py --check` fails the commit on one -- so 17 minutes is the
right price. Before the manifest this happened by accident, because any change
to the single key meant a full render anyway.

## Structured data on the generated pages

Every generated page type but two carries a schema.org block from
`build_pages.json_ld()`: `ScholarlyArticle` for a paper, `Person`,
`Organization`, `Periodical`, `Dataset` and, for a patent, `CreativeWork`
(schema.org has no Patent type), and the home page a `Dataset` for the
catalog itself. Family and application-area pages have none.

**A method page's `@type` follows what the catalog knows**, because the
`algorithm` rows span software, reviews, benchmarks and written-up workflows
and no one type is honest for all of them. A recorded repository makes it
`SoftwareSourceCode`, with the repository as `codeRepository`. Every other row
is a `DefinedTerm` in the catalog's `DefinedTermSet`, which claims only that
the catalog names and defines it: PEAKS is commercial software with no
repository, and manual interpretation is not software at all. Both carry the
DESCRIBING papers as `subjectOf`, never the ones that only ran the method.
A wrong `@type` is worse than none.

## IndexNow: telling Bing what changed

Google reads `sitemap.xml`; Bing, Yandex, Seznam and Naver also take
**IndexNow** pushes, and Bing's index is what Copilot answers from. After the
gh-pages push, `publish.yml` runs `indexnow.py submit`, which POSTs the changed
URLs to `api.indexnow.org`.

**The list comes from the `.qmd` hashes, not from what was rendered.**
`render_scope.py` compares each page's source with the restored manifest and
writes `.indexnow-urls` (outside `_site`, gitignored), whatever the render
scope. A full render forced by a Quarto upgrade rewrites every page and changes
what none of them says, and submitting thousands of unchanged URLs is exactly
what the protocol asks sites not to do. The home page is added only when some
page changed, since its build date moves on every run. The daily repo-metrics
commit therefore submits nothing.

**The key is public by design.** Ownership is proved by serving
`<key>.txt` containing the key, so anyone can read it; `postrender.py` writes it
into `_site` on every run, because a full render empties the directory. It
sits under the project path rather than at the domain root, which `keyLocation`
allows and which restricts the key to this site's URLs. The step waits up to
five minutes for gh-pages to serve the file before submitting, since a key that
cannot be fetched is answered 403, and every failure is a warning: the site is
live by then, and a lost ping costs crawl latency, nothing more.

## URLs are a lock file

`slugs.lock` records the published URL of every generated page. A slug is
derived from mutable data, so editing a title or a name silently rewrites a URL,
404s the old one and discards its search equity. `python3 scripts/slugs.py --check`
fails on a CHANGED or REMOVED entry and passes on an ADDED one; it runs in
`.githooks/pre-commit` and in `check-slugs.yml`.

A **changed or removed** URL is never auto-fixed: it fails the commit, because
rewriting the baseline for those is the very failure being guarded against.
When a rename is intended, run `--write` and let the lock diff record it.

**An UNMATCHED count also fails the commit**, and for the same reason a
changed slug does. `check_counts.py --fix` repairs a stale NUMBER and exits 0,
but it cannot repair a claim whose PATTERN no longer matches the prose, and for
that it exits 1. The hook used to discard that status with `|| true`, so
rewording a sentence in CLAUDE.md -- an ordinary thing to do, and something
`--fix` does itself -- could silently unmatch the regex that reads a number out
of it. The registry entry then checks nothing and nobody hears about it until
CI goes red; two commits went out that way. The auto-fix and the re-staging
still run first, so a refused commit keeps the numbers `--fix` just corrected.

**A VIOLATED invariant fails the commit too, and is never rewritten.** Some
claims state the value the data must have, always zero (family pages with
no note, for one), and `INVARIANTS` in `check_counts.py` names them. `--fix`
used to treat them like any count, so when the Protease strategy family gained
a page with no note it rewrote that guard from zero to one in the same commit,
and the check passed. Now a nonzero invariant prints `VIOLATED`, leaves the prose
alone and exits 1 under `--fix` as well, so the remedy has to be to the data.
A label in `INVARIANTS` that names no claim is an error, so a renamed claim
cannot drop out of the set unnoticed.

**A renamed URL redirects; it is never just dropped.** `REDIRECTS` in
`slugs.py` maps each retired slug to its successor, and `build_pages.py`
gives the successor page a Quarto `aliases:` entry, which writes a redirect
page at the old address. Add to it and never prune it. The first 21 came from
publisher markup: 24 titles carry the paper's own `<i>`, `<sub>` or JATS
`<italic>`, which slugged to `...-i-de-novo-i-...`, and two journal names
stored as `&amp;` had venue pages of their own. `slugify()` now strips tags
and entities, the two journal names were decoded into the real venues, and
the lock was rewritten with `--write`.

**A second name gets a URL too, from `ALIAS_URLS`, not from the aliases
column.** Casanovo Foundation was published in MCP as CasanovoTL, so
`pages/algorithms/casanovotl.html` redirects to its page, by the same Quarto
`aliases:` stub. The list is curated because `algorithm.aliases` also holds
descriptions ("18O labeling", "guanidination; sulfonation") and every entry is
a permanent URL. `slugs.py --check` fails if any `REDIRECTS` or `ALIAS_URLS`
entry names a live page, which its stub would overwrite, or points at a page
that does not exist. Search is separate: `postrender.py` appends every
algorithm's "Also known as" value to its search record, so all 28 aliases
(PandaNovo, PrimeNovo, CasanovoTL...) find their method from the navbar.

**The markup is kept in the data and rendered, not stripped.** It is the
paper's own typography, and the publication page's heading already showed it
in italics while every list escaped it into literal tags. `title_md()` in
`build_pages.py` and the `title_html` OJS cell in `index.qmd` escape
everything except `<i>`, `<b>`, `<sub>` and `<sup>` (JATS `<italic>` and
`<bold>` normalised to those), and drop any other tag; JSON-LD gets the
title as plain text.

A **new** URL is recorded automatically by the hook, right after `--check`
passes. That is safe precisely because the dangerous cases already failed by
then, and the alternative is worse: `--check` passes on additions, so the lock
quietly stopped being a complete inventory. Publication 352's page was live and
in the sitemap while absent from the lock, which means a later rename of it
would not have been caught at all.

The hook used to skip both guards entirely unless `denovo.db` was staged, which
had the same effect for a different reason: adding the `families` entity type
put 24 new URLs on the site from a commit that touched no data, and the hook
returned before `slugs.py` ran. The dump step is still conditional; the count
refresh and the URL guard are not.
