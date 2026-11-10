# The Quarto site

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## "Recently added" is publication.id, not a date

The site's **Recently added** list, first thing under Browse all papers, answers
"what changed since I last looked" without the catalog storing a second date per
row. `publication.id` is handed out by SQLite in insertion order, so ordering by
it descending IS the order papers arrived; the same id is the sortable `#`
column in the full table, which is how to see past the three the list shows.

**It shows three, and every link that means "the papers" skips it.** The list
sits directly under the `Browse all papers` heading, so `#browse-all-papers`
landed a reader on recent arrivals rather than on the table the link promised.
The navbar's `Papers` entry, the hero `papers` counter, the kind-breakdown rows
and `ANCHORS["browse-papers"]` in `build_pages.py` -- which every generated
page's footer uses -- now all point at **`#every-paper`**, the table's own
heading. Three places still point at `#browse-all-papers` and should: the
section's heading anchor and its TOC entry, which are navigation TO the section,
and nothing else. The `Browse all authors` link needs no equivalent, because
that section opens on its table.

The prose above the list reads its count back out of the array, so shortening it
again cannot leave the text claiming ten.

Two things to keep straight. The date shown beside each entry is the PAPER's
publication date, not when it was catalogued, and the list says so, because
several additions each month are older work that surfaced in a
`build_candidates.py` sweep. And the highest id can exceed the row count, since
a deleted row does not give its id back -- 1001 against 1000 publications today --
so the id is an ordering, never a count.

A real `added_at` column would be better and is not worth it: the value only
exists in git, backfilling it means an `-S` search per row over the whole
history, and every future insert would have to remember to set it. The ordering
is free and cannot drift.

**The table itself sorts on the full DATE, descending, not on the year.**
`Inputs.table`'s sort is stable, so sorting on a year leaves the rows within
each year in the array's own order -- here the SQL's
`ORDER BY p.publication_date`, oldest first. The table therefore opened on
January 2026 and a paper added today landed sixty rows below it, in a column
that said 2026 all the way down. The column is now the ISO date as a STRING,
which sorts lexicographically, matches "2026-09" in the search box, and needs no
formatter. The BibTeX export still reads `year` off the row, which is in the
data whether or not it is a column.

## The navbar and the hero badges

The navbar is **The map, Papers, Methods, Applications, Datasets, Patents,
Authors**. Patents points at the section `#patents`, since a patent has no page
of its own here.
Applications points at the section `#application-areas`, whose lane labels
link each area's page; it was added because downstream applications are 349 of
the 816 method rows, the largest kind, and had no way in from the navbar. It is
one link rather than a dropdown of the 17 areas, which would be a hand-kept
copy of the `subdomain` table. Papers points at
`#every-paper` and Datasets at the SECTION `#the-data-underneath`, and the
difference is deliberate: the papers section opens with a short list of recent
arrivals standing in front of the table, while the datasets section opens with
the charts a reader actually wants. Methods points at the architectures swim
lane, the only view showing every method, which is the target the hero badge
already used.

The fourth hero badge is **datasets**, replacing countries. `n_countries` stays
defined, because the geography section's own prose and the summary sentence both
still read it.

**The breakdown rows already filter, and the anchor was the bug.** Clicking
`Benchmarks` under *By kind* sets `kind_filter` and jumps to the table; it had
always done the first half, but it jumped to `#browse-all-papers`, which landed
the reader on the recently-added list where nothing visibly happened. Verified
after the anchor change by clicking in headless Chrome: the table goes 50 rows
to 18, every row reads `benchmark`, one of seven kind checkboxes is checked, and
the filter panel opens so the change is reversible.

**The Code filter says "No repository recorded", never "closed source".**
It is the fourth control in the sticky filter bar, beside Kind, Approach and
Acquisition, and it reads `algorithm_repository`, which is curated and
incomplete: 109 of 824 methods have a row, 55 of the 76 deep-learning
algorithms. A method whose code exists but is not catalogued lands in the
second bucket, so the label states what the catalog knows rather than a fact
about the method. `repository_candidates.csv` is where the gap closes.

A publication passes on its DESCRIBING links only (`has_code` in the Python
chunk), not on `repo IS NOT NULL`: `repo` concatenates every linked method's
repository, `'uses'` links included, so a venomics paper that ran Casanovo
would have counted as open source. Measured in headless Chrome: 356 swim-lane
dots split 68 with code and 288 without, and 976 papers split 135 and 841,
0 OJS errors.

## Working with the notebook

`plots.ipynb` is kept as an offline exploration / sanity-check tool only. It writes PNGs into `plots/` via `plt.savefig(...)` but those PNGs are **not committed** (the interactive Quarto site (`index.qmd`) replaces them). Use the notebook for ad-hoc SQL exploration or to cross-check what the Quarto site renders.

## The Quarto site

`index.qmd` + `_quarto.yml` produce an interactive site published to GitHub Pages at <https://jeroen.vangoey.be/awesome_de_novo_peptide_sequencing/>. Architecture:

- A **single Python chunk** at the top of `index.qmd` queries `denovo.db` and hands each dataset to OJS: the big ones as `data/<name>.json` files, the rest inline through `ojs_define(...)`. See **The homepage loads its data as files** below.
- **OJS cells** call Quarto's built-in `transpose()` to convert column-oriented data into row-oriented arrays, then render with **Observable Plot** (bars / scatter / timeline) and **d3-force** (co-authorship network). Every counter, axis label, and prose number flows from those datasets; never hardcode anything in the .qmd.
- `.github/workflows/publish.yml` rebuilds on every push to `main` and pushes to the `gh-pages` branch via `quarto-actions/publish@v2`. Cache is via `astral-sh/setup-uv@v3`; no PAT needed (uses `GITHUB_TOKEN`).

### The homepage loads its data as files

`ojs_define` inlines every value as JSON inside `index.html`, and by 2026-10
that was **7.9 MB of an 8.4 MB page**. Measured with mobile Lighthouse from a
GitHub runner (`lighthouse.yml`, 3 runs, median): score 6, first paint 10.4 s,
largest paint 14.0 s, 15.0 s of blocking time, interactive at 40.5 s.

**The big datasets are files now.** `DATA_FILES` in the first Python chunk
names the 15 that are written to `data/<name>.json` (gitignored, regenerated
every render) with Quarto's own DataFrame conversion copied verbatim, and one
OJS block of `FileAttachment(...).json()` cells loads them, so every chart
reads exactly the value it read before. Quarto copies a file referenced by
`FileAttachment` into `_site` by itself. The page paints before any data
arrives: 645 KB of HTML instead of 8.4 MB. **An inline value over 100 KB fails
the render**, naming itself, so the next big table cannot land in the HTML
unnoticed; it needs a `DATA_FILES` entry AND a cell, and a name with no cell is
a ReferenceError that the chart audit counts.

`perf_points` was 3.2 MB on its own, 2.5 MB of it tooltip provenance that
repeated each paper's title, year and type in all 7000 entries, plus a cue
sentence that was never displayed. The tooltip now looks the paper up in
`pub_lookup` by id: 1.8 MB.

**A force layout ticks only while its chart is on screen.** Throttled 4x, the
three force charts blocked the main thread for 2.7 s of every 5 s from second
5 to second 45, sections below anything visible: 24 s of blocking time in all.
`run_when_visible` starts each simulation stopped and resumes it whenever its
chart is within 200 px of the viewport, until it settles. Blocking time went
from 24.0 s to 4.5 s, all of it in the first 5 s, building 41 charts.

**A chart is built only when it is about to be seen.** Building all 41 at load
still left 39,023 DOM elements and 9.9 s of main thread on mobile Lighthouse,
and it ran the accessibility checker out of time: PageSpeed then reports every
accessibility and SEO audit as "Error!", `<title>` and `lang` included, which
is a crash and not 70 failures. Each chart cell is now
`lazy_chart("key", () => { ...body... })`, a generator that shows a
placeholder of the chart's measured height (`lazy_heights`, at 412 and 1600 px,
interpolated) and builds the chart when the placeholder is within 600 px of the
viewport. Its data cells still run at load. Measured locally at 4x CPU: 30
charts deferred, the DOM at load 39,023 to 8,822 elements, busy time 7.2 s to
3.4 s. **A new chart cell should use it**, with its heights added to the map;
`check_chart_overlap.py` scrolls to every `[data-lazy]` placeholder before
measuring and fails if one never builds. Only a cell nothing else reads can be
wrapped, since its value becomes the placeholder first: the papers table,
`viewof selected_pubs`, is not.

**No Python cell may display HTML.** Quarto treats any `text/html` display
output from a Jupyter cell as a JavaScript widget and puts require.js and jQuery
from jsdelivr in the `<head>`, render-blocking; `ojs_define` is such an output,
and it cost about 1.2 s of first paint for two libraries nothing used. The
inline values are printed as a raw `<script type="ojs-define">` from the
`output: asis` chunk `ojs-define` instead, which the OJS runtime reads all the
same. Print raw HTML from an `asis` chunk rather than calling `display(HTML(...))`.

**Quarto's own head scripts are deferred on the home page.** `postrender.py`
adds `defer` to the ten classic `<script src="site_libs/...">` tags in its
`<head>`, which Lighthouse charged about 1.1 s of render blocking. Safe because
every inline use of them, Quarto's `quarto-html-after-body` included, runs in a
DOMContentLoaded handler, which deferred scripts precede. Verified in headless
Chrome: navbar, search (43 results for "Casanovo"), tooltips and the 36 heading
anchors all work. Re-check after a Quarto upgrade, which may add a script that
runs inline.

**CSS that shapes the first screen goes in the `<head>`.** With nothing
blocking in the head, the browser paints before parsing reaches the style
blocks in the body, so a rule there arrives one frame late. The rule hiding
the title block's duplicate description was one: the description showed for a
frame, 128 px tall, then vanished, and CLS went from 0.024 to 0.085-0.126. That
rule, the hero and breakdown slot heights and the scope banner are now an
`include-in-header` style in index.qmd's front matter; recorded frame by frame
under throttling, CLS is 0.006. Preloading the fonts took CLS to 0.000 and
cost a second of LCP on Lighthouse's slow 4G, so they are not preloaded; the
front matter records both numbers.

**The OJS source and inline values are files, not part of the page.** They
were 85% of the home page's HTML (433 KB of base64 source, 141 KB of values),
and Lighthouse paints nothing before the document has arrived. `postrender.py`
moves every `script[type=ojs-module-contents]` and `script[type=ojs-define]`
into `_site/ojs/<kind>.<hash>.json` and makes Quarto's bootstrap fetch them
and put the same elements back just before `interpretFromScriptTags()`. The
page went from 194 KB to 21 KB gzipped. Hashed names keep a page and its files
together through GitHub Pages' ten-minute cache, and `ojs/manifest.json` keeps
the last five generations of each so a page still cached from an earlier
publish finds its own. The source is no less public for it: it is one request
away instead of inline.

**Accessibility and SEO read 100 locally, and four fixes got them there.** Once
axe could finish, it found markup no option controls: Observable's inspector
draws its expand toggle as an `<a>` with no href (about 120 hidden ones, which
the SEO audit counts as uncrawlable links), and Inputs.table and Inputs.range
leave their checkboxes and sliders unlabelled. A small inline script beside
the hero styles, installed before the OJS runtime starts, gives each toggle
`role="button"` and each control an `aria-label` as it appears (13 ms at 4x
CPU), and removes Quarto's invalid `role="menu"` from the navbar toggler. The
↗ links are `.ext-link`, a 24 px touch target, and the citation blockquote is
#57606a, since Bootstrap's grey is 4.39:1. Still open inside a BUILT chart,
where load-time Lighthouse does not look: Plot puts `aria-label` on bare `<g>`
elements, and the SVG links on chart marks have no accessible name.

**The two cells above the scope banner hold their space before they render.**
The hero counters and the kind/approach breakdown are drawn by OJS once the
data arrives, and empty they let the banner and all the prose under it jump
down by about 500 px: that was the page's entire layout-shift score, CLS
0.41. `.hero-slot` and `.breakdown-slot` give the two cells a `min-height`
measured in headless Chrome at 412, 800 and 1280 px, in index.qmd's own
`{=html}` style block rather than `custom.scss`, which would force a full
render. CLS went from 0.41 to 0.006. Re-measure the heights if either cell
gains a row.

**No data file is requested before the first frame is painted.** Lighthouse's
default mobile numbers are SIMULATED: it records one unthrottled load and
estimates a slow phone from it. When chart work happened to start before the
first frame in that recording, the estimate charged all of it to the
subtitle, the page's largest paint, so LCP read 4.7 s or 11.8 s from run to
run while a real browser, throttled 4x, painted the subtitle at 0.96 s either
way. The `first_frame` cell (two animation frames, or 1 s) gates the 15 file
cells, which costs a reader one frame. A cell names `first_frame` to wait on
it: OJS hands other cells the RESOLVED value, so `first_frame.then(...)` was
179 errors and an empty page.

**A performance budget is checked weekly.** `lighthouse.yml` runs mobile
Lighthouse three times on the live home page every Tuesday at 11:00 UTC (and
on demand from the Actions tab) and checks the MEDIAN against LCP 9 s, total
blocking time 9 s, CLS 0.1 and 1 MB of uncompressed HTML: roughly 1.5 to 2x
the medians after the fixes above (4.7 s, 6.4 s, 0.005, 648 KB), because single
runs vary a lot. Total blocking time counts only work after first paint, so
the first-frame gate RAISED it from 3.8 s to 6.4 s without adding any work,
which is why its budget is 9 s rather than the 7 s first set. The page before them broke all four. A breach opens
"Site performance budget exceeded", assigned to the owner, comments on it
while it stays open and closes it on the first run back inside. It never fails
a publish: the site is live by then, so a red run would undo nothing. Measure
from there, not from the maintainer's machine, whose gateway decompresses
HTML and makes every local run look several times worse.

The consequence for `check_chart_overlap.py`: it now scrolls every
`.chart-wrap` into view in turn and waits for its dots to stop moving before
measuring, because a layout that never ran is not a layout worth auditing.

### The two network charts filter on papers per author, with a slider

The co-authorship network and the author-to-model graph used to hold every
author with three or more papers. After the backlog and the citation sweep that
was 382 authors and 376 author nodes, and both layouts collapsed into one ball.
Each chart now has a **Min papers per author** slider, 3 to 15, defaulting to
5. The query floor stays at 3 so the slider can go down to it, and
`author_papers` carries each author's count to the page. Measured in headless
Chrome: 180 and 147 circles at the default, 415 and 376 at 3, 54 and 36 at 10,
0 OJS errors. They are two sliders rather than one shared control because the
charts are a section apart.

The author-to-model graph also has **Max authors/paper**, because one
consortium paper links all its authors to one model: at the default it removes
exactly the 58 of 907 links that exist only through papers of more than 20
authors (verified, 907 to 849 lines). It has no **Min strength**, which
measures ties between co-authors, and that graph has none. Both Max sliders
range up to `max_paper_authors`, the largest byline, computed from the data.
That was a constant, 60, written when the largest paper had 53 authors, so
once 62- and 66-author papers arrived the collaboration network's "no cutoff"
default was silently dropping them.

### OJS source is public, Python chunk source is not

`echo: false` hides a cell's source from the rendered *page*, but Quarto still
embeds every **OJS** cell's source in `_site/index.html` TWICE: once base64 in
the `ojs-module-contents` script, which the runtime evaluates, and once as a
highlighted `<pre>` inside `.sourceCode.cell-code.hidden`, hidden by CSS and
read by nothing. Search engines indexed that second copy as the page's text,
`${first_year}` and `viewof` definitions included, and Quarto copied it into
`search.json`. `postrender.py` now deletes it (163 blocks, 1.6 MB of the
10 MB page) and rebuilds the home page's search records from the stripped
page; verified in headless Chrome that every chart still draws with 0 OJS
errors. The base64 copy stays, so an OJS comment still ships to every
visitor, decodable by anyone who looks. **Python** chunk comments really are
dropped (verified both ways: a distinctive comment from the Python chunk
appears 0 times in both files, one from an OJS cell appeared in both).

**The page's numbers are all drawn by OJS**, so a crawler that runs no
JavaScript sees the prose with every count missing. The `crawler-metadata`
chunk gives it two things, both read from `.zenodo.json` and `denovo.db`: a
schema.org `Dataset` block (what Google Dataset Search reads: creator with
ORCID, DOI, licence, downloads, dates) and a `<noscript>` summary with the
counts.

Consequence: any note you do not want published (an unpublished analysis, a TODO
naming people, a half-finished finding) belongs in the Python chunk, not beside
the chart it describes. The network-statistics notes at the end of the Python
chunk in `index.qmd` are there for exactly this reason and say so.

### Every family gets a lane, and one switch turns it into the history chart

The architectures swim lane draws **every** family, in chronological order of
first appearance, with a lane exactly as tall as its labels need. Getting there
took three separate fixes, all of them still worth knowing:

**Every present family gets a lane.** The lane list used to be intersected with
a hand-written list of the 13 busiest families, so a family outside those 13 was
dropped from the chart entirely. The checkbox list offers all 49, so unchecking
everything and checking one of the other 36 produced an **empty chart**: 36
families and 76 of the 192 methods were unreachable.

**Lane order is chronological**, so the chart reads as the field's history
whether it shows every method or only each family's first. It is computed over **all** entries, not the
filtered ones, so a lane keeps its place as the filters change. The array runs
**latest-first** because lanes stack upward from `y = 0`, which means the array
is walked in order and NOT reversed: reversing put `Sparse autoencoder` on top
and `Heuristic` at the bottom, the opposite of chronological order. Verified in the
rendered SVG, top to bottom: Heuristic, Graph / DP, Sequence tag, Neural
network, Chemical labeling assisted, ... Flow, Palaeoproteomics workflow, Sparse
autoencoder.

**Lane height comes from packing, not from a table.** Both swim lanes now lay
their dots out in PIXELS: a label's width is estimated from its character count,
and a greedy first-fit pass reuses a row only when two labels cannot touch. A
lane is then `rows * 43 px` tall, 43 being two label lines plus the dot. The old
scheme assigned a tier from a date gap against a tier list sized to the BUSIEST
family, which handed a 2-unit lane 38 tier positions ~4 px apart and stacked its
labels on top of each other. `band_heights` and `subdomain_lane_height` are gone
with it; `band_color` and `subdomain_color` remain, as colour registries only.

**Every family also gets its own colour, from ONE scale.** The other 36 used to
share a single neutral grey, so most of the chart read as one undifferentiated
family. There were also three separate family-colour maps on the page, which is
what `family_color_scale` exists to prevent, so all three now defer to it: the
swim lane, the code-activity scatter and the author-model graph.

It has to colour **71** families: 49 real ones plus the 22 pseudo-families the
author-model graph mints for a family-less method (`Reviews`,
`Adjacent tools (misc)`, `Application: venomics`). Ten keep a hand-tuned value.
The rest are assigned from a grid of 24 hues at two CIE lightnesses and two
chromas, 96 candidates, by a farthest-point walk: each family takes the
candidate furthest in Lab from every colour already assigned, with distance to
its neighbours in the chronological walk as a tiebreaker.

Both halves of that were learned by measuring the wrong thing first. A single
24-hue ring at one lightness ran out after 24 and started reusing, and weighting
the walk-neighbour term above the global one gave good lane contrast and a
legend of alternating greens and purples. Now: 50 legend entries, 50 distinct
colours, exactly one pair under 10 deltaE, worst adjacent lane pair 23.9.
`Heuristic`, `Graph / DP`, `Learning-to-rank` and `Flow` lost their pinned
values on purpose -- the first three were near-identical greys at the top of the
chart, and Flow's `#937DC2` sat 5.8 deltaE from CNN's `#8172B3`, the worst pair
on the page.

LCh and not HSL throughout, because HSL at one lightness renders yellow far
paler than blue, and these colours carry a 10 px bold label as well as a dot.
Assigned over ALL families, so a family keeps its colour as filters change.

Measured: the default all-checked view is **49 lanes / 201 dots / 5779 px**, and
filtering to one family renders one lane at the 240 px floor (verified for
`Sequence tag`, 5 dots on one row).

**"The long view" is now a mode of this chart, not a chart of its own.** It
was one row per family at the first publication of its earliest method, which
is this swim lane with every method but the first removed, drawn a second
time. The **First appearance only** toggle does that removal instead:
- each lane keeps its earliest method among whatever the other filters leave;
- lanes shrink to one 26 px row, labelled beside the dot, flipping left near
  the right edge (two `Plot.text` marks, because dx is a constant);
- the dot grows with the number of methods the family went on to collect,
  which needs `r: {type: "identity"}`.

The chart, its table and the `family_firsts` query are gone. Measured: 52
dots in 1,412 px against 231 in 6,768, and 0 collisions in either mode. The
**nine years of quiet between 1981 and 1990** still read as a gap, because
the x axis is still time: `Heuristic` arrives with PAAS in April 1981 and the
second family, `Graph / DP`, not until Bartels in June 1990.

That gap used to be written here as 1984 to 1994, and it moved because the
catalog gained PAAS (1981) and Improved PAAS (1983). **PAAS 3 was the earliest
row in the catalog and was never the earliest program**: its own name says it is
the third, and the Osaka group published PAAS in 1981 and Improved PAAS in 1983,
neither of which was here. Its `short_description` said "one of the earliest",
which was the accurate hedge; promoting it to "the earliest" would have been
wrong twice over. The three are three `algorithm` rows rather than one versioned
row, which keeps PAAS 3's published URL and costs two near-identical pages.

A new family works with no registration at all: it gets a lane, a generated
colour and a packed height. Add it to `band_color` only if you want a specific
colour for it. `SELECT DISTINCT algorithm_family FROM algorithm` is the list.

### Hand-written analysis pages

`pages/` is gitignored and rebuilt by `build_pages.py` on every publish, and
`render_scope.py` re-renders only index.qmd and what is under `pages/`. So a
page written by hand cannot sit at the root: it would need a `_quarto.yml`
change (a full render) to exist, and its later edits would never reach the fast
paths. Instead its source is committed under `analysis/<name>/` and
`build_pages.py` copies it to `pages/analyses/<slug>.qmd` as the `analyses`
kind, from the `ANALYSES` registry. Its `site_data.json` is INLINED as the OJS
value `ns`, so a change to the numbers changes the page's hash and the next
publish re-renders it. `postrender.py` gives it a search record ("Analysis"),
and the dataset page named in the registry links to it. One so far:
`nine-species-versions`, linked from the homepage's *The data underneath*.

**Every number on an analysis page is computed, never typed.** The analysis's
`page_numbers.py` registers one function per number, with the public sources
it reads (keys of its `sources.py`) and the scripts its input comes from, and
writes `page_numbers.json`. `build_pages.py` replaces each `{{n:<id>}}` in
`page.qmd` with the number as a link to its entry in a generated *Reproducing
every number* appendix (`{{appendix}}`), which prints the sources, the scripts
and the function's own code. An id the JSON lacks fails the build, and an id
must be lower case. The nine-species page is reproducible from public sources
by `run_all.sh`; the owner chose scripts over publishing derived PSM files.

Two traps met building it. In OJS `name = {...}` is a BLOCK, not an object
literal, so it needs parentheses. And `check_chart_overlap.py` counted only the
runtime's error class, so an OJS SYNTAX error, which Quarto prints as a callout
reading "OJS Syntax Error", passed as 0 errors. It now counts that text too, and
takes a page path: `check_chart_overlap.py pages/analyses/nine-species-versions.html`.

### A family page needs two methods

`build_pages.py` generates a page per architecture family, but only for the
**33 of 50** families that hold two or more methods. The other **17** hold
exactly one method, and a page for one of those would have carried that method's
papers, that method's authors and that method's dates: a copy of a page that
already exists, on a permanent indexed URL. Those cover **341** of the **358**
methods that carry a family. Contrast the application areas, where five
singletons still got a page each, because even a one-workflow area aggregates
papers, authors and countries that no other page collects.

The threshold is **derived, not curated**: `HAVING COUNT(*) >= 2`, written three
times -- in `slugs.py`'s `ENTITY_QUERIES`, in `build_pages.py`'s loader and in
`index.qmd`'s `_key_sql` -- because each of the three needs the member set for a
different reason and importing one into the others would couple the site's
Python chunk to the generator. So a family reaching two methods gains a page
with no other edit, which `slugs.py --check` reports as an ADDED slug and
passes. A family falling back to one method REMOVES a published URL: `--check`
fails and `render_scope.py` demands a full render. Both are the intended
loudness.

Keyed by `MIN(id)` over the family's `algorithm` rows, the same value-keyed
pattern institutions and venues use. There is deliberately no `family` table to
key on: `algorithm_family` is free text, and a table would have to be
hand-edited on every membership change. The cost is that deleting a family's
earliest algorithm row moves the key and so rewrites the URL.

Everything on the page comes through the `'describes'` links, for the same
reason the algorithm pages split on role: the snake-venom papers that ran
Casanovo are not papers about transformers.

**No lane label is dead.** `family_href` sends a family with a page to that page
and a one-method family straight to its single method, so every label in the
architectures swim lane is clickable, in either mode, and a reader never has to know which of the two kinds of target they
got.

**The one thing the page cannot derive is prose**, so `family_note` holds it:
`(name, blurb)`, one line per family on what its methods share, keyed by the
string `algorithm.algorithm_family` carries. A separate table rather than a
column on `algorithm`, because the fact is about the family and a column would
repeat it on all 37 Transformer (AR) rows with nothing keeping the copies equal.
It holds nothing but prose on purpose: membership is still the HAVING clause and
the URL is still `MIN(id)`, so a note cannot invent a family or move its page.

All 33 families with a page have a note. The two ways that can rot are
registered in `check_counts.py` as invariants that must read zero, the same
shape as the `subdomain` pair: 0 pages without a note, 0 notes without a page.
A missing note is otherwise invisible, because the page simply falls back to the
generated sentence.

The blurb doubles as the page's `<meta name="description">`, which is why each
one is written to fit inside the 250-character clip with the family name
prefixed. It is stored with a plain "de novo" and italicised at render, exactly
like the `subdomain` blurbs, since asterisks in a meta tag render literally.

### Plot's dx, dy and textAnchor are constants, not channels

Passing a function to `dx`, `dy` or `textAnchor` on a `Plot.text` mark applies
**neither**: Plot reads them once as constants, so a function silently degrades
to `dx: 0` and `textAnchor: "middle"`. Nothing warns, because a function is a
perfectly legal value to hand an option.

This is what put a dot in the middle of all 49 names in the former "long view"
chart, now the swim lane's first-appearance mode, which splits its marks the
same way. The
mark meant "labels right of the dot, except late ones, which flip left", written
as `dx: d => flips(d) ? -10 : 10`. What rendered was every label centred on its
own dot: 49 label-over-dot collisions, 9 px each, measured in the DOM. It also
made the marginRight measurement that accompanied it wrong, since a centred
label overflows half as far as a right-anchored one.

**Split the mark in two** and give each a constant, which is what the chart does
now: `Plot.text(rows.filter(d => !flips(d)), {dx: 11, textAnchor: "start"})` and
`Plot.text(rows.filter(flips), {dx: -11, textAnchor: "end"})`. 11 px clears the
largest dot, whose radius is `3 + 9 * 0.55`.

### No chart on the page has an overlapping label

Verified by measurement, not by eye. `check_chart_overlap.py` serves `_site`,
drives it in headless Chrome over CDP and, for all 20 chart SVGs, counts
text-text overlaps, text-over-dot overlaps and text outside the SVG frame from
real `getBoundingClientRect()` boxes. All three are **0**, re-audited after a
further 90 s so the two force simulations are included honestly. Run it after
any chart change:

```bash
uv run quarto render index.qmd          # it measures _site, so render first
uv run --with websockets python3 scripts/check_chart_overlap.py
```

It is deliberately NOT in the pre-commit hook or CI: it needs a browser and a
rendered site, and takes about a minute.

Four classes of defect turned up, and each has its own remedy in `index.qmd`:

1. **Swim-lane tiers** that assumed a lane was tall enough. Replaced by the
   pixel row packing described above.
2. **`dx`/`textAnchor` as functions**, above.
3. **An axis label on the tick baseline.** Plot puts the x-axis label level with
   the tick labels, where it touched a tick by 3 px in five charts. Fixed with
   `labelOffset: 40` and `marginBottom: 48`, which gives the label its own line.
4. **Estimated label widths that were too small.** Every greedy placer reserves
   a box from a character count, so an underestimate is an invisible licence to
   overlap: one estimate was even capped at 150 px while the text kept going,
   which hid a 67 px overhang. Estimates are now 6.2-7.0 px per character at
   10-12 px, and the citation-flow chart truncates a label at 26 characters so
   the estimate cannot drift far. If you add a label, re-run the audit rather
   than trusting the constant.

**Data growth breaks a clean audit, so re-run it after a bulk import.** The
radar backfill left five charts colliding with no chart code touched. The
application-to-sequencer flow had passed 100 sequencers against a 1200 px cap,
83 overlapping labels. Folding the tail into one "N more sequencers" node fixed
the audit and hid the long tail the Cites view exists to show, so it was
undone: every sequencer has its own row, heights stay proportional to traffic,
and a node too thin for its label gets white space below it rather than
height. Cites draws 106 sequencers in 1700 px, Uses 20 in 530 px, both with 0
overlaps, measured in each view since the audit only sees the default. The
"Either" signal is gone too: it put a curated "ran PEAKS" and a bare citation
of the PEAKS paper into one ribbon of one width, and answered neither
question. The dataset network measures each label with a
canvas instead of 5.8 px per character, and keeps 2 px clear of every dot. The
lifecycle bars needed 285 px of left margin, not 270. 41 charts, 0 collisions.

In the two force-directed charts every name cannot be shown at once: measured
123 and 457 overlapping label pairs. A greedy pass in descending degree (model
names first in the bipartite) tries six positions per name -- right of the dot,
left of it, and each nudged a line up or down -- and hides the name if none is
free, so about 100 of 173 authors and 103 of 360 bipartite nodes carry a visible
label. Nothing is lost: every node answers a hover with its full name, and the
hidden ones are the least connected. The pass is O(n^2), so it runs every fourth
tick rather than on all of them.

### Clamping a force layout hides the bug; it does not fix it

Both force charts clamp node positions in the tick handler, which stopped nodes
drifting off-canvas. It also produced a **worse-looking chart**: a ring of nodes
parked on the frame with an empty middle, 23% of the co-authorship nodes and 26%
of the bipartite's sitting within 4 px of an edge. A clamp turns divergence into
a neat pile against the wall, and the pile is the tell.

Two causes, both fixed by making the layout actually converge:

- **`forceManyBody` with no `distanceMax`** repels every pair at any distance, so
  the outward pressure grows with the square of the node count while the
  positional forces stay constant. At 173 and 360 nodes the equilibrium was
  wider than the frame. Capped at 170 px and 200 px, the repulsion only
  separates neighbours, which is all it was ever for.
- **Cluster targets on a ring.** Each affiliation (and each family, in the
  bipartite) was pulled toward `cos/sin(i/n)` at 0.3 of the frame, so all 108
  targets sat on one ellipse and none in the middle. They are now on a
  phyllotaxis disc, `r = sqrt((i + 0.5) / n)` at the golden angle, which is
  area-uniform and so fills the interior.

Measured after: 0 of 173 and 2 of 360 nodes near an edge, x spanning 122-1231 and
240-1299 of 1400. The bipartite also needed a taller frame, 650 to 820 px: at 360
nodes the density alone kept 19 of them pinned to the top and bottom however the
forces were tuned, which is a signal to give a graph more canvas rather than more
force. The clamp stays as a guard, and now almost never binds.

The knobs that matter, in the order worth trying: `distanceMax` on the charge,
then the shape of the positional targets, then the frame size, and only then the
force strengths. `wall_probe.js` (in the scratchpad, not the repo) counts nodes
within 4 px of the frame and reports the position spread, which is the number to
tune against -- the label-overlap audit passes happily on a ring.

### Plot tooltips truncate the value, not the label

`Plot`'s `tip` renders each line as "label value" and truncates it at
`lineWidth`, which defaults to 20em, roughly 40 characters. The ellipsis eats
the END of the line, which is the value. The Publication-lifecycle bars hit
this: the x channel is labelled from the SCALE, "Preprint → peer-reviewed gap
(months)" is 37 characters, and the tooltip showed no number at all, just "…".

`plot_tip_style` now sets `lineWidth: 34`, which is safe because none of the
five charts that share it put free text such as a paper title in a tip. Where a
channel's own name would be wrong or ugly, name it explicitly with `channels`
and switch the raw one off in `format`: the lifecycle bars pass
`channels: { "Method": "label", "Gap (months)": "gap_months" }` with
`format: { x1: false, x2: false, y: false }`, because `y` has no scale label and
Plot otherwise falls back to the field name, giving "label DiffuNovo".
