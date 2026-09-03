#!/usr/bin/env python3
"""Generate one Quarto page per catalog entity.

The site is otherwise a single page where every entity exists only as a mark or
a table row, so there is nowhere to land: a reader who spots an author in the
collaboration network cannot click through to see what else they wrote. This
writes a page per publication, author, algorithm, institution and venue, with
every entity name on it a link to another such page.

Two deliberate departures from the other build_*.py scripts:

  * It writes FILES, not tables. It must never be wired into
    .github/actions/commit-refreshed-db, whose one-table-per-workflow invariant
    it does not satisfy and whose `git reset --hard origin/main` on a push race
    would delete its output.
  * It needs no network, so it is safe (and intended) to run in CI on every
    publish, unlike the offline metric builders.

DETERMINISM IS A HARD REQUIREMENT. `quarto publish gh-pages` commits the whole
_site tree at least daily, and git only stores a new blob when a file's bytes
change. So:

  * no build timestamp on any generated page (index.qmd's footer keeps the
    build date; that is one file, one blob a day);
  * every query carries an explicit total ORDER BY, and no Python set is
    iterated into output;
  * volatile metrics that refresh daily (repository stars) are deliberately
    NOT baked into pages -- see the note on the algorithm template;
  * generated files get a deterministic mtime, because Quarto's sitemap.xml
    records the INPUT file's mtime as <lastmod>, so fresh mtimes would rewrite
    all ~1969 sitemap entries on every CI run.

Slugs come from slugs.py, shared with whatever links INTO these pages, so the
two can never disagree.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from slugs import all_slugs

DB_PATH = Path(__file__).parent / "denovo.db"
OUT_ROOT = Path(__file__).parent / "pages"

KINDS = ("publications", "authors", "algorithms", "institutions", "venues")

# Anchors on index.qmd, verified against the rendered section ids.
ANCHORS = {
    "browse-papers":   ("Browse all papers", "browse-all-papers"),
    "browse-authors":  ("Browse all authors", "browse-all-authors"),
    "citations":       ("How the field cites itself", "how-the-field-cites-itself"),
    "impact":          ("Academic impact by citation count", "academic-impact-by-citation-count"),
    "lifecycle":       ("Publication lifecycle", "publication-lifecycle"),
    "architectures":   ("The architectures", "the-architectures"),
    "applications":    ("Application areas", "application-areas"),
    "code":            ("Code activity", "code-activity"),
    "collaboration":   ("The collaboration network", "the-collaboration-network"),
    "bipartite":       ("Models and the authors behind them", "models-and-the-authors-behind-them"),
    "geography":       ("Where the work happens", "where-the-work-happens"),
    "venues":          ("Where it appears", "where-it-appears"),
}

# A publication's dominant kind, resolved with the SAME priority order index.qmd
# uses, so a detail page cannot contradict the charts.
KIND_PRIORITY = (
    "downstream-application", "review", "benchmark", "meta",
    "post-processor", "adjacent", "algorithm",
)

# Every generated file gets an mtime derived from its content date, never "now".
EPOCH_FALLBACK = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()


def md_escape(text: str | None) -> str:
    """Escape the characters that actually occur in this catalog's data.

    Measured across titles, names, departments, journals and descriptions: <, >,
    ", ', &, ~ (pandoc subscript), _, *, [, ], !, \\, |. Escaping only what
    occurs keeps the output readable rather than a wall of backslashes.
    """
    if not text:
        return ""
    text = str(text)
    for ch in ("\\", "*", "_", "[", "]", "<", ">", "~", "|", "`", "#"):
        text = text.replace(ch, "\\" + ch)
    return text.replace("\n", " ").strip()


def yaml_quote(text: str | None) -> str:
    """Double-quoted YAML scalar, safe for titles containing colons and quotes."""
    if text is None:
        return '""'
    text = str(text).replace("\\", "\\\\").replace('"', '\\"')
    text = re.sub(r"\s+", " ", text).strip()
    return f'"{text}"'


def italicise_de_novo(text: str) -> str:
    """Italicise the Latin phrase in TEMPLATED prose only.

    Per CLAUDE.md: never inside a copied paper title, an identifier or a DB
    string literal, which is why this is applied to our own sentences and never
    to `md_escape`d data.
    """
    return re.sub(r"\bde novo\b", "*de novo*", text)


class Site:
    """Holds the slug tables and renders cross-page links."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.slugs = all_slugs(conn)
        self.slugs.pop("__fallbacks__", None)

    def href(self, kind: str, entity_id: int | None, *, from_kind: str) -> str | None:
        if entity_id is None:
            return None
        slug = self.slugs.get(kind, {}).get(int(entity_id))
        if not slug:
            return None
        # Sibling directories under pages/, so one level up then across.
        return f"../{kind}/{slug}.html" if kind != from_kind else f"{slug}.html"

    def link(self, kind: str, entity_id: int | None, label: str, *, from_kind: str) -> str:
        href = self.href(kind, entity_id, from_kind=from_kind)
        text = md_escape(label)
        return f"[{text}]({href})" if href else text

    def home(self, anchor_key: str | None = None) -> str:
        if anchor_key is None:
            return "../../index.html"
        label, anchor = ANCHORS[anchor_key]
        return f"../../index.html#{anchor}"

    def seen_in(self, keys: list[str]) -> list[str]:
        out = ["", "## Seen in the charts", ""]
        for key in keys:
            label, anchor = ANCHORS[key]
            out.append(f"- [{label}](../../index.html#{anchor})")
        out += ["", "[Back to the full map](../../index.html)", ""]
        return out


def front_matter(title: str, subtitle: str | None = None) -> list[str]:
    lines = ["---", f"title: {yaml_quote(title)}"]
    if subtitle:
        lines.append(f"subtitle: {yaml_quote(subtitle)}")
    lines += [
        "toc: false",
        # These files are generated and gitignored; an "Edit this page" link
        # would point at a path that does not exist in the repo.
        "repo-actions: false",
        "---",
        "",
    ]
    return lines


def dominant_kind(kinds: list[str]) -> str | None:
    for candidate in KIND_PRIORITY:
        if candidate in kinds:
            return candidate
    return kinds[0] if kinds else None


def date_to_mtime(date_str: str | None) -> float:
    if not date_str:
        return EPOCH_FALLBACK
    try:
        return datetime.fromisoformat(str(date_str)[:10]).replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return EPOCH_FALLBACK


# --------------------------------------------------------------------------
# Page templates
# --------------------------------------------------------------------------

def render_publication(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    K = "publications"
    L = []
    kind = dominant_kind(ctx["kinds"])
    bits = [row["publication_type"] or "publication"]
    if row["journal"]:
        bits.append(row["journal"])
    if row["publication_date"]:
        bits.append(str(row["publication_date"])[:4])
    L += front_matter(row["title"], " · ".join(bits))

    L.append("| | |")
    L.append("|---|---|")
    if row["publication_date"]:
        L.append(f"| Date | {row['publication_date']} |")
    L.append(f"| Type | {md_escape(row['publication_type'])} |")
    if row["journal"]:
        venue_id = ctx["venue_ids"].get(row["journal"])
        L.append(f"| Venue | {site.link('venues', venue_id, row['journal'], from_kind=K)} |")
    if row["publisher"]:
        L.append(f"| Publisher | {md_escape(row['publisher'])} |")
    if kind:
        L.append(f"| Contribution | {md_escape(kind)} |")
    if row["doi"]:
        L.append(f"| DOI | [{md_escape(row['doi'])}](https://doi.org/{row['doi']}) |")
    elif row["url"]:
        L.append(f"| Link | [{md_escape(row['url'])}]({row['url']}) |")
    if ctx["cited_by_count"] is not None:
        L.append(f"| Citations (OpenAlex) | {ctx['cited_by_count']} |")
    if ctx["venue_citedness"] is not None:
        L.append(f"| Venue 2-year citedness | {ctx['venue_citedness']:.2f} |")
    L.append("")

    if ctx["counterpart"]:
        other, relation = ctx["counterpart"]
        L += ["::: {.callout-note appearance=\"simple\"}",
              f"**{relation}:** "
              + site.link("publications", other["id"], other["title"], from_kind=K)
              + f" ({str(other['publication_date'])[:10]}"
              + (f", {md_escape(other['journal'])}" if other["journal"] else "") + ")",
              ":::", ""]

    if row["abstract"]:
        L += ["## Abstract", "", md_escape(row["abstract"]), ""]

    if ctx["authors"]:
        L += ["## Authors", ""]
        for author_id, name, affs in ctx["authors"]:
            line = "1. " + site.link("authors", author_id, name, from_kind=K)
            if affs:
                inst_links = ", ".join(
                    site.link("institutions", inst_id, inst_name, from_kind=K)
                    for inst_id, inst_name in affs
                )
                line += f" · {inst_links}"
            L.append(line)
        L.append("")

    if ctx["algorithms"]:
        L += ["## Methods and tools", ""]
        for alg_id, name, descr in ctx["algorithms"]:
            line = "- " + site.link("algorithms", alg_id, name, from_kind=K)
            if descr:
                line += f": {md_escape(descr)}"
            L.append(line)
        L.append("")

    for heading, edges in (("Cites", ctx["cites"]), ("Cited by", ctx["cited_by"])):
        if not edges:
            continue
        L += [f"## {heading} ({len(edges)})", ""]
        for other_id, title, date, source in edges:
            L.append(
                f"- {site.link('publications', other_id, title, from_kind=K)}"
                f" ({str(date)[:4]}) <small>{md_escape(source)}</small>"
            )
        L.append("")

    keys = ["browse-papers"]
    if ctx["cites"] or ctx["cited_by"]:
        keys.append("citations")
    if ctx["cited_by_count"]:
        keys.append("impact")
    if ctx["counterpart"]:
        keys.append("lifecycle")
    L += site.seen_in(keys)
    return "\n".join(L) + "\n", date_to_mtime(row["publication_date"])


def render_author(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    K = "authors"
    L = []
    n = len(ctx["pubs"])
    sub = f"{n} paper{'s' if n != 1 else ''} in the catalog"
    if ctx["countries"]:
        sub += " · " + ", ".join(ctx["countries"])
    L += front_matter(row["display_name"], sub)

    if ctx["affiliations"]:
        L += ["## Affiliations", ""]
        for inst_id, inst_name, dept in ctx["affiliations"]:
            line = "- " + site.link("institutions", inst_id, inst_name, from_kind=K)
            if dept:
                line += f" · {md_escape(dept)}"
            L.append(line)
        L.append("")
    # No email address here, deliberately. These pages are crawlable and listed
    # in sitemap.xml, so a mailto: on each of 78 author pages is an invitation to
    # scrapers. The addresses are already in the papers themselves, which is
    # where someone who needs to make contact should get them. Public profile
    # links carry no address and are fine.
    ids = []
    if row["scholar_id"]:
        ids.append(f"[Google Scholar](https://scholar.google.com/citations?user={row['scholar_id']})")
    if row["sciprofiles_id"]:
        ids.append(f"[SciProfiles](https://sciprofiles.com/profile/{row['sciprofiles_id']})")
    if ids:
        L += ["## Elsewhere", "", " · ".join(ids), ""]

    if ctx["pubs"]:
        L += ["## Papers", ""]
        for pub_id, title, date, journal in ctx["pubs"]:
            line = f"- {site.link('publications', pub_id, title, from_kind=K)}"
            meta = [str(date)[:4]] if date else []
            if journal:
                meta.append(md_escape(journal))
            if meta:
                line += f" ({', '.join(meta)})"
            L.append(line)
        L.append("")

    if ctx["algorithms"]:
        L += ["## Methods and tools", "",
              ", ".join(site.link("algorithms", a_id, name, from_kind=K)
                        for a_id, name in ctx["algorithms"]), ""]

    if ctx["coauthors"]:
        L += [f"## Co-authors ({len(ctx['coauthors'])})", "",
              italicise_de_novo(
                  "Ranked by Newman fractional collaboration strength, so a pair "
                  "on a two-author paper counts for more than a pair on a "
                  "50-author consortium paper."), ""]
        for other_id, name, strength, shared in ctx["coauthors"][:40]:
            L.append(
                f"- {site.link('authors', other_id, name, from_kind=K)}"
                f" <small>strength {strength:.2f}, {shared} shared</small>"
            )
        L.append("")

    keys = ["browse-authors"]
    if n >= 3:   # the `prolific` CTE threshold used by coauth_edges / author_affs
        keys += ["collaboration", "bipartite"]
    if ctx["affiliations"]:
        keys.append("geography")
    L += site.seen_in(keys)
    latest = max((p[2] for p in ctx["pubs"] if p[2]), default=None)
    return "\n".join(L) + "\n", date_to_mtime(latest)


def render_algorithm(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    K = "algorithms"
    L = []
    sub_bits = [b for b in (row["kind"], row["algorithm_family"]) if b]
    L += front_matter(row["name"], " · ".join(sub_bits) if sub_bits else None)

    if row["short_description"]:
        L += [md_escape(row["short_description"]), ""]

    L += ["| | |", "|---|---|"]
    for label, value in (
        ("Kind", row["kind"]),
        ("Family", row["algorithm_family"]),
        ("Deep learning", None if row["is_deep_learning"] is None
                          else ("yes" if row["is_deep_learning"] else "no")),
        ("Acquisition", row["acquisition_mode"]),
        ("Application area", row["subdomain"]),
        ("Also known as", row["aliases"]),
    ):
        if value:
            L.append(f"| {label} | {md_escape(str(value))} |")
    L.append("")

    # Repository URLs are stable, but stars / open issues / last-push refresh
    # DAILY. Baking them in would rewrite every algorithm page every day, so
    # only the URL goes here; the live numbers stay on the Code activity chart.
    if ctx["repos"]:
        L += ["## Code", ""]
        for url in ctx["repos"]:
            L.append(f"- <{url}>")
        if ctx["has_metrics"]:
            L += ["", "Live stars, open issues and last-push figures are on the "
                  f"[Code activity chart]({site.home('code')}).", ""]
        else:
            L += ["", "Not tracked on the Code activity chart: those figures come "
                  "from the GitHub API, and this link is not a public GitHub "
                  "repository.", ""]

    if ctx["pubs"]:
        L += ["## Papers", ""]
        for pub_id, title, date, journal, ptype in ctx["pubs"]:
            line = f"- {site.link('publications', pub_id, title, from_kind=K)}"
            meta = [m for m in (str(date)[:4] if date else None, journal, ptype) if m]
            if meta:
                line += f" ({md_escape(', '.join(meta))})"
            L.append(line)
        L.append("")

    if ctx["authors"]:
        L += [f"## Authors ({len(ctx['authors'])})", "",
              ", ".join(site.link("authors", a_id, name, from_kind=K)
                        for a_id, name in ctx["authors"]), ""]

    # Only claim a chart the entry actually appears on. The architectures
    # swim-lane bands by algorithm_family and drops family-less rows; the code
    # charts read repository_metrics; the bipartite graph needs an author with
    # 3+ papers.
    keys = []
    if row["algorithm_family"]:
        keys.append("architectures")
    if row["subdomain"]:
        keys.append("applications")
    if ctx["has_metrics"]:
        keys.append("code")
    if ctx["has_prolific_author"]:
        keys.append("bipartite")
    L += site.seen_in(keys)
    earliest = min((p[2] for p in ctx["pubs"] if p[2]), default=None)
    return "\n".join(L) + "\n", date_to_mtime(earliest)


def render_institution(site: Site, name: str, ctx: dict) -> tuple[str, float]:
    K = "institutions"
    L = []
    sub = []
    if ctx["places"]:
        sub.append(", ".join(ctx["places"]))
    sub.append(f"{len(ctx['authors'])} author{'s' if len(ctx['authors']) != 1 else ''}")
    L += front_matter(name, " · ".join(sub))

    if ctx["departments"]:
        L += ["## Departments", ""]
        for dept in ctx["departments"]:
            L.append(f"- {md_escape(dept)}")
        L.append("")

    if ctx["authors"]:
        L += [f"## Authors ({len(ctx['authors'])})", "",
              ", ".join(site.link("authors", a_id, nm, from_kind=K)
                        for a_id, nm in ctx["authors"]), ""]

    if ctx["pubs"]:
        L += [f"## Papers ({len(ctx['pubs'])})", ""]
        for pub_id, title, date, journal in ctx["pubs"]:
            meta = [m for m in (str(date)[:4] if date else None, journal) if m]
            L.append(f"- {site.link('publications', pub_id, title, from_kind=K)}"
                     + (f" ({md_escape(', '.join(meta))})" if meta else ""))
        L.append("")

    L += site.seen_in(["geography", "browse-authors"])
    latest = max((p[2] for p in ctx["pubs"] if p[2]), default=None)
    return "\n".join(L) + "\n", date_to_mtime(latest)


def render_venue(site: Site, name: str, ctx: dict) -> tuple[str, float]:
    K = "venues"
    L = []
    L += front_matter(name, f"{len(ctx['pubs'])} paper"
                            f"{'s' if len(ctx['pubs']) != 1 else ''} in the catalog")
    if ctx["impact"]:
        two_yr, h_index, works = ctx["impact"]
        L += ["| | |", "|---|---|"]
        if two_yr is not None:
            L.append(f"| 2-year mean citedness | {two_yr:.2f} |")
        if h_index is not None:
            L.append(f"| h-index | {h_index} |")
        if works is not None:
            L.append(f"| Works indexed | {works} |")
        L += ["", italicise_de_novo(
            "From OpenAlex. The 2-year mean citedness is computed the same way "
            "as the Journal Impact Factor, but over the open citation graph."), ""]

    L += ["## Papers", ""]
    for pub_id, title, date, ptype in ctx["pubs"]:
        meta = [m for m in (str(date)[:4] if date else None, ptype) if m]
        L.append(f"- {site.link('publications', pub_id, title, from_kind=K)}"
                 + (f" ({md_escape(', '.join(meta))})" if meta else ""))
    L.append("")

    keys = ["venues"]
    if ctx["impact"]:
        keys.append("browse-papers")
    L += site.seen_in(keys)
    latest = max((p[2] for p in ctx["pubs"] if p[2]), default=None)
    return "\n".join(L) + "\n", date_to_mtime(latest)


# --------------------------------------------------------------------------
# Data loading. One query per relation, all with total ORDER BY clauses.
# --------------------------------------------------------------------------

def load(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    q = conn.execute
    d: dict = {}

    d["publications"] = [dict(r) for r in q(
        "SELECT id, title, publication_date, publication_type, publisher, journal, "
        "doi, url, abstract FROM publication ORDER BY id"
    )]
    d["authors"] = [dict(r) for r in q(
        "SELECT id, display_name, scholar_id, sciprofiles_id "
        "FROM author_display ORDER BY id"
    )]
    d["algorithms"] = [dict(r) for r in q(
        "SELECT id, name, algorithm_family, short_description, kind, "
        "is_deep_learning, acquisition_mode, aliases, subdomain "
        "FROM algorithm ORDER BY id"
    )]

    d["pub_authors"] = defaultdict(list)
    d["author_pubs"] = defaultdict(list)
    for r in q("SELECT pa.publication_id, pa.author_order, a.id AS aid, a.display_name, "
               "p.title, p.publication_date, p.journal "
               "FROM publication_author pa "
               "JOIN author_display a ON a.id = pa.author_id "
               "JOIN publication p ON p.id = pa.publication_id "
               "ORDER BY pa.publication_id, pa.author_order, a.id"):
        d["pub_authors"][r["publication_id"]].append((r["aid"], r["display_name"]))
        d["author_pubs"][r["aid"]].append(
            (r["publication_id"], r["title"], r["publication_date"], r["journal"]))

    d["author_insts"] = defaultdict(list)
    d["inst_authors"] = defaultdict(list)
    d["inst_places"] = defaultdict(list)
    d["inst_depts"] = defaultdict(list)
    for r in q("SELECT aa.author_id, af.name AS inst, af.department, "
               "MIN(af.id) OVER (PARTITION BY af.name) AS inst_key, "
               "ci.name AS city, co.name AS country, a.display_name "
               "FROM author_affiliation aa "
               "JOIN affiliation af ON af.id = aa.affiliation_id "
               "JOIN author_display a ON a.id = aa.author_id "
               "LEFT JOIN city ci ON ci.id = af.city_id "
               "LEFT JOIN country co ON co.id = af.country_id "
               "ORDER BY af.name, af.department, a.display_name"):
        d["author_insts"][r["author_id"]].append(
            (r["inst_key"], r["inst"], r["department"]))
        pair = (r["author_id"], r["display_name"])
        if pair not in d["inst_authors"][r["inst"]]:
            d["inst_authors"][r["inst"]].append(pair)
        place = ", ".join(x for x in (r["city"], r["country"]) if x)
        if place and place not in d["inst_places"][r["inst"]]:
            d["inst_places"][r["inst"]].append(place)
        if r["department"] and r["department"] not in d["inst_depts"][r["inst"]]:
            d["inst_depts"][r["inst"]].append(r["department"])

    d["inst_key"] = {}
    for r in q("SELECT name, MIN(id) AS k FROM affiliation GROUP BY name ORDER BY name"):
        d["inst_key"][r["name"]] = r["k"]

    d["pub_algs"] = defaultdict(list)
    d["alg_pubs"] = defaultdict(list)
    for r in q("SELECT pa.publication_id, a.id AS aid, a.name, a.short_description, "
               "p.title, p.publication_date, p.journal, p.publication_type, a.kind "
               "FROM publication_algorithm pa "
               "JOIN algorithm a ON a.id = pa.algorithm_id "
               "JOIN publication p ON p.id = pa.publication_id "
               "ORDER BY pa.publication_id, a.name, a.id"):
        d["pub_algs"][r["publication_id"]].append(
            (r["aid"], r["name"], r["short_description"], r["kind"]))
        d["alg_pubs"][r["aid"]].append(
            (r["publication_id"], r["title"], r["publication_date"],
             r["journal"], r["publication_type"]))

    d["repos"] = defaultdict(list)
    for r in q("SELECT algorithm_id, url FROM algorithm_repository "
               "ORDER BY algorithm_id, sort_order, url"):
        d["repos"][r["algorithm_id"]].append(r["url"])

    # Which repo URLs build_repo_metrics.py could actually resolve. It uses the
    # gh CLI, so a PyPI page, a lab website, an anonymous-review link, a
    # Hugging Face Space or a not-yet-public GitHub repo will never have a row,
    # and 8 algorithms are in that position. Without this the page promised
    # "live stars on the Code activity chart" for repos that can never appear
    # there.
    d["metric_urls"] = {r["url"] for r in
                        q("SELECT url FROM repository_metrics ORDER BY url")}

    # The co-authorship and author-algorithm charts draw only authors with 3+
    # papers (the `prolific` CTE in index.qmd), so an algorithm reaches the
    # bipartite graph only through one of them.
    d["prolific"] = {r["author_id"] for r in
                     q("SELECT author_id FROM publication_author "
                       "GROUP BY author_id HAVING COUNT(*) >= 3 ORDER BY author_id")}

    d["cites"] = defaultdict(list)
    d["cited_by"] = defaultdict(list)
    for r in q("SELECT pc.citing_id, pc.cited_id, pc.source, "
               "ci.title AS citing_title, ci.publication_date AS citing_date, "
               "cd.title AS cited_title, cd.publication_date AS cited_date "
               "FROM publication_citation pc "
               "JOIN publication ci ON ci.id = pc.citing_id "
               "JOIN publication cd ON cd.id = pc.cited_id "
               "ORDER BY pc.citing_id, pc.cited_id"):
        d["cites"][r["citing_id"]].append(
            (r["cited_id"], r["cited_title"], r["cited_date"], r["source"]))
        d["cited_by"][r["cited_id"]].append(
            (r["citing_id"], r["citing_title"], r["citing_date"], r["source"]))

    d["impact"] = {r["publication_id"]: r["cited_by_count"] for r in
                   q("SELECT publication_id, cited_by_count FROM publication_impact "
                     "ORDER BY publication_id")}
    d["journal_impact"] = {r["journal"]: (r["two_yr_citedness"], r["h_index"],
                                          r["works_count"]) for r in
                           q("SELECT journal, two_yr_citedness, h_index, works_count "
                             "FROM journal_impact ORDER BY journal")}

    d["versions"] = {}
    for r in q("SELECT preprint_id, published_id FROM publication_version "
               "ORDER BY preprint_id"):
        d["versions"][r["preprint_id"]] = ("Peer-reviewed version", r["published_id"])
        d["versions"][r["published_id"]] = ("Preprint version", r["preprint_id"])

    d["venue_pubs"] = defaultdict(list)
    for r in q("SELECT journal, id, title, publication_date, publication_type "
               "FROM publication WHERE journal IS NOT NULL AND journal <> '' "
               "ORDER BY journal, publication_date DESC, id"):
        d["venue_pubs"][r["journal"]].append(
            (r["id"], r["title"], r["publication_date"], r["publication_type"]))
    d["venue_key"] = {}
    for r in q("SELECT journal, MIN(id) AS k FROM publication "
               "WHERE journal IS NOT NULL AND journal <> '' "
               "GROUP BY journal ORDER BY journal"):
        d["venue_key"][r["journal"]] = r["k"]

    # Newman fractional co-authorship strength, same formula the site uses.
    sizes = {r["publication_id"]: r["n"] for r in
             q("SELECT publication_id, COUNT(*) AS n FROM publication_author "
               "GROUP BY publication_id ORDER BY publication_id")}
    strength: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in q("SELECT pa1.author_id AS a1, pa2.author_id AS a2, pa1.publication_id AS p "
               "FROM publication_author pa1 JOIN publication_author pa2 "
               "ON pa1.publication_id = pa2.publication_id AND pa1.author_id <> pa2.author_id "
               "ORDER BY pa1.author_id, pa2.author_id, pa1.publication_id"):
        n = sizes.get(r["p"], 2)
        if n < 2:
            continue
        slot = strength[r["a1"]][r["a2"]]
        slot[0] += 1.0 / (n - 1)
        slot[1] += 1
    names = {r["id"]: r["display_name"] for r in
             q("SELECT id, display_name FROM author_display ORDER BY id")}
    d["coauthors"] = {}
    for a1, others in strength.items():
        d["coauthors"][a1] = sorted(
            ((a2, names[a2], v[0], v[1]) for a2, v in others.items()),
            key=lambda t: (-t[2], t[1]))

    # Newest first, publication id as a deterministic tie-break.
    #
    # These lists are accumulated from queries ordered by publication_id, which
    # is INSERTION order and only loosely chronological: a 2025 journal paper
    # entered today gets a higher id than a 2026 preprint entered last week. On
    # Lukas Kall's page that put his 2025 J Proteome Research paper AFTER two
    # 2026 papers. Every tuple here happens to carry the date at index 2 and the
    # publication id at index 0, so one pass fixes all four.
    def by_date_desc(rows: list[tuple]) -> list[tuple]:
        return sorted(rows, key=lambda r: (str(r[2] or ""), r[0]), reverse=True)

    for bucket in ("author_pubs", "alg_pubs", "cites", "cited_by"):
        d[bucket] = {k: by_date_desc(v) for k, v in d[bucket].items()}

    return d


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=KINDS, metavar="KIND",
                        help="generate only this entity type (repeatable)")
    parser.add_argument("--out", type=Path, default=OUT_ROOT,
                        help=f"output root (default {OUT_ROOT.name}/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report counts, write nothing")
    args = parser.parse_args()
    kinds = tuple(args.only) if args.only else KINDS

    conn = sqlite3.connect(DB_PATH)
    site = Site(conn)
    d = load(conn)

    written: dict[str, int] = {}

    # Per-directory metadata, written by the generator so CI needs nothing
    # committed under pages/. search: false keeps ~1969 thin pages out of
    # search.json, which every visitor downloads before their first keystroke.
    # Little is lost: index.qmd's own "Browse all papers" / "Browse all authors"
    # tables already search the same data, with filters, and more usefully.
    # Measured: this does NOT speed up the render (67.8s vs 64.9s over 234
    # pages, i.e. noise); the win is purely index size, 508 KB back down to
    # 156 KB.
    METADATA = (
        "# Generated by build_pages.py. Do not edit.\n"
        "search: false\n"
        "repo-actions: false\n"
        "toc: false\n"
    )

    def emit(kind: str, slug: str, body: str, mtime: float) -> None:
        written[kind] = written.get(kind, 0) + 1
        if args.dry_run:
            return
        path = args.out / kind / f"{slug}.qmd"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = path.parent / "_metadata.yml"
        if not meta.exists():
            meta.write_text(METADATA, encoding="utf-8")
            os.utime(meta, (EPOCH_FALLBACK, EPOCH_FALLBACK))
        path.write_text(body, encoding="utf-8")
        os.utime(path, (mtime, mtime))

    if "publications" in kinds:
        for row in d["publications"]:
            pid = row["id"]
            authors = []
            for aid, name in d["pub_authors"].get(pid, []):
                insts = [(k, nm) for k, nm, _dept in d["author_insts"].get(aid, [])]
                seen, uniq = set(), []
                for k, nm in insts:
                    if nm not in seen:
                        seen.add(nm)
                        uniq.append((k, nm))
                authors.append((aid, name, uniq))
            algs = [(a, n, desc) for a, n, desc, _k in d["pub_algs"].get(pid, [])]
            counterpart = None
            if pid in d["versions"]:
                relation, other_id = d["versions"][pid]
                other = next((p for p in d["publications"] if p["id"] == other_id), None)
                if other:
                    counterpart = (other, relation)
            ji = d["journal_impact"].get(row["journal"] or "")
            ctx = {
                "kinds": [k for *_x, k in d["pub_algs"].get(pid, [])],
                "authors": authors,
                "algorithms": algs,
                "cites": d["cites"].get(pid, []),
                "cited_by": d["cited_by"].get(pid, []),
                "cited_by_count": d["impact"].get(pid),
                "venue_citedness": ji[0] if ji else None,
                "venue_ids": d["venue_key"],
                "counterpart": counterpart,
            }
            body, mtime = render_publication(site, row, ctx)
            emit("publications", site.slugs["publications"][pid], body, mtime)

    if "authors" in kinds:
        for row in d["authors"]:
            aid = row["id"]
            affs = d["author_insts"].get(aid, [])
            countries = []
            for _k, nm, _dept in affs:
                for place in d["inst_places"].get(nm, []):
                    country = place.split(", ")[-1]
                    if country not in countries:
                        countries.append(country)
            algs, seen = [], set()
            for pub_id, *_rest in d["author_pubs"].get(aid, []):
                for a, n, _desc, _k in d["pub_algs"].get(pub_id, []):
                    if n not in seen:
                        seen.add(n)
                        algs.append((a, n))
            ctx = {
                "pubs": d["author_pubs"].get(aid, []),
                "affiliations": affs,
                "countries": countries,
                "algorithms": sorted(algs, key=lambda t: t[1]),
                "coauthors": d["coauthors"].get(aid, []),
            }
            body, mtime = render_author(site, row, ctx)
            emit("authors", site.slugs["authors"][aid], body, mtime)

    if "algorithms" in kinds:
        for row in d["algorithms"]:
            gid = row["id"]
            authors, seen = [], set()
            for pub_id, *_r in d["alg_pubs"].get(gid, []):
                for a, name in d["pub_authors"].get(pub_id, []):
                    if name not in seen:
                        seen.add(name)
                        authors.append((a, name))
            repos = d["repos"].get(gid, [])
            ctx = {
                "pubs": d["alg_pubs"].get(gid, []),
                "repos": repos,
                "authors": sorted(authors, key=lambda t: t[1]),
                "has_metrics": any(u in d["metric_urls"] for u in repos),
                "has_prolific_author": any(a in d["prolific"] for a, _n in authors),
            }
            body, mtime = render_algorithm(site, row, ctx)
            emit("algorithms", site.slugs["algorithms"][gid], body, mtime)

    if "institutions" in kinds:
        for name, key in sorted(d["inst_key"].items()):
            pubs, seen = [], set()
            for aid, _nm in d["inst_authors"].get(name, []):
                for pub_id, title, date, journal in d["author_pubs"].get(aid, []):
                    if pub_id not in seen:
                        seen.add(pub_id)
                        pubs.append((pub_id, title, date, journal))
            ctx = {
                "authors": d["inst_authors"].get(name, []),
                "departments": d["inst_depts"].get(name, []),
                "places": d["inst_places"].get(name, []),
                "pubs": sorted(pubs, key=lambda t: (str(t[2] or ""), t[0]), reverse=True),
            }
            body, mtime = render_institution(site, name, ctx)
            emit("institutions", site.slugs["institutions"][key], body, mtime)

    if "venues" in kinds:
        for name, key in sorted(d["venue_key"].items()):
            ctx = {"pubs": d["venue_pubs"].get(name, []),
                   "impact": d["journal_impact"].get(name)}
            body, mtime = render_venue(site, name, ctx)
            emit("venues", site.slugs["venues"][key], body, mtime)

    total = sum(written.values())
    print("Done. " + ", ".join(f"{v} {k}" for k, v in sorted(written.items()))
          + f" = {total} pages total.")
    if args.dry_run:
        print("--dry-run: nothing was written.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
