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
import html
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from itertools import groupby
from statistics import median
from datetime import datetime, timezone
from pathlib import Path

from slugs import REDIRECTS, all_slugs

DB_PATH = Path(__file__).parent / "denovo.db"


def _site_url() -> str:
    """Read site-url from _quarto.yml so the two cannot drift apart."""
    text = (Path(__file__).parent / "_quarto.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*site-url:\s*(\S+)\s*$", text, re.M)
    if not m:
        raise SystemExit("build_pages: no site-url in _quarto.yml")
    return m.group(1).rstrip("/") + "/"


SITE_URL = _site_url()
OUT_ROOT = Path(__file__).parent / "pages"

KINDS = ("publications", "authors", "algorithms", "institutions", "venues",
         "subdomains", "families", "datasets")

# Anchors on index.qmd, verified against the rendered section ids.
ANCHORS = {
    # Points at the TABLE, not at the section heading. The section opens with
    # the 'Recently added' list, so landing on #browse-all-papers put a reader
    # who followed "Browse all papers" from a method or author page on a list
    # of the newest arrivals instead of on the table they were after.
    "browse-papers":   ("Browse all papers", "every-paper"),
    "datasets":        ("The data underneath", "the-data-underneath"),
    "browse-authors":  ("Browse all authors", "browse-all-authors"),
    "citations":       ("How the field cites itself", "how-the-field-cites-itself"),
    "impact":          ("Academic impact by citation count", "academic-impact-by-citation-count"),
    "lifecycle":       ("Publication lifecycle", "publication-lifecycle"),
    "architectures":   ("The architectures", "the-architectures"),
    "benchmarks":      ("How they score", "how-they-score"),
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


# The inline markup publishers put in titles, as Crossref and JATS deliver it:
# "<i>de novo</i>", "cytochrome <i>c</i><sub>4</sub>", "<italic>Saccharomyces
# pastorianus</italic>". It is the paper's own typography, so it is RENDERED,
# not stripped from the data: 24 stored titles carry it, and their URL slugs
# were derived with it, so cleaning the data would have rewritten 19 published
# URLs. md_escape used to escape it on every list page while the publication
# page's own heading (YAML title) rendered it, so one title showed in italics
# on its page and as literal <i> tags in every list that linked to it.
TITLE_TAGS = {"i": "i", "em": "i", "italic": "i", "b": "b", "strong": "b",
              "bold": "b", "sub": "sub", "sup": "sup"}


def title_md(text: str | None) -> str:
    """A paper title for Markdown: escaped, except for the publisher's inline tags."""
    out = md_escape(text)
    def keep(m: re.Match) -> str:
        tag = TITLE_TAGS.get(m.group(2).lower())
        return f"<{m.group(1)}{tag}>" if tag else m.group(0)
    out = re.sub(r"\\<(/?)(\w+)\\>", keep, out)
    return re.sub(r"\s+", " ", out).strip()


def title_tags(text: str | None) -> str:
    """A title for a raw-HTML context (the page heading): tags normalised, others dropped."""
    def keep(m: re.Match) -> str:
        tag = TITLE_TAGS.get(m.group(2).lower())
        return f"<{m.group(1)}{tag}>" if tag else ""
    t = re.sub(r"<(/?)(\w+)[^>]*>", keep, text or "")
    return re.sub(r"\s+", " ", t).strip()


def strip_markup(text: str | None) -> str:
    """A title as plain text, for JSON-LD and anywhere tags would show literally."""
    t = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", t).strip()


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
        text = title_md(label) if kind == "publications" else md_escape(label)
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


def clip(text: str | None, limit: int = 155) -> str:
    """One-line summary trimmed on a word boundary, for a meta description."""
    if not text:
        return ""
    t = " ".join(str(text).split())
    if len(t) <= limit:
        return t
    # Strip a trailing period too, or a cut landing just after one yields
    # "...the spectrum...." with four dots.
    return t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + "..."


def join_sentences(parts: list[str]) -> str:
    """Join fragments with ". ", without doubling an existing terminator.

    Needed because a fragment can legitimately end in a period ("Eloff et
    al."), and a naive ". ".join produces "Eloff et al.. Mass spectrometry".
    """
    out = ""
    for part in [p for p in parts if p]:
        if not out:
            out = part
        elif out.endswith((".", "!", "?", "\u2026")):
            out += " " + part
        else:
            out += ". " + part
    return out


def json_ld(obj: dict) -> list[str]:
    """A schema.org block for the page.

    Google reads `description`, not `og:description`, for snippets, and reads
    JSON-LD to understand what an entity is. Both were missing: every generated
    page inherited the single site-level description, so 2000+ pages offered
    identical text.

    Deterministic by construction: sorted keys, no timestamps, nothing that can
    reorder between runs. "<" is escaped so a title containing "</script>"
    cannot close the block early.
    """
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).replace("<", "\u003c")
    return ["```{=html}",
            f'<script type="application/ld+json">{payload}</script>',
            "```", ""]


def front_matter(title: str, subtitle: str | None = None,
                 description: str | None = None) -> list[str]:
    lines = ["---", f"title: {yaml_quote(title)}"]
    if subtitle:
        lines.append(f"subtitle: {yaml_quote(subtitle)}")
    if description:
        lines.append(f"description: {yaml_quote(description)}")
    lines += [
        "toc: false",
        # These files are generated and gitignored; an "Edit this page" link
        # would point at a path that does not exist in the repo.
        "repo-actions: false",
        "---",
        "",
    ]
    return lines


def with_canonical(body: str, url: str) -> str:
    """Insert a per-page <link rel="canonical"> into a generated page's front matter.

    Quarto has no canonical-URL option of its own; site-url only drives og:url
    and the sitemap. A page-level include-in-header MERGES with the
    project-level one rather than replacing it (verified: og:title and
    og:description survive), so this is purely additive.

    These pages each have exactly one URL form, so this is prevention rather
    than a fix -- the duplicate Google reported was the home page, which is
    reachable both as `.../` and `.../index.html`.
    """
    end = body.index("\n---\n")
    block = ("\nformat:\n"
             "  html:\n"
             "    include-in-header:\n"
             "      - text: |\n"
             f'          <link rel="canonical" href="{url}">')
    return body[:end] + block + body[end:]


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
    author_names = [nm for _aid, nm, _affs in ctx["authors"]]
    desc_bits = [" · ".join(bits)]
    if author_names:
        desc_bits.append(author_names[0] + (" et al." if len(author_names) > 1 else ""))
    if row["abstract"]:
        desc_bits.append(clip(row["abstract"], 110))
    L += front_matter(title_tags(row["title"]), " · ".join(bits),
                      clip(join_sentences(desc_bits), 250))

    ld = {"@context": "https://schema.org", "@type": "ScholarlyArticle",
          "headline": strip_markup(row["title"]), "name": strip_markup(row["title"])}
    if row["publication_date"]:
        ld["datePublished"] = str(row["publication_date"])
    if author_names:
        ld["author"] = [{"@type": "Person", "name": nm} for nm in author_names]
    if row["journal"]:
        ld["isPartOf"] = {"@type": "Periodical", "name": row["journal"]}
    if row["publisher"]:
        ld["publisher"] = {"@type": "Organization", "name": row["publisher"]}
    if row["doi"]:
        ld["identifier"] = f"https://doi.org/{row['doi']}"
        ld["sameAs"] = f"https://doi.org/{row['doi']}"
    if row["abstract"]:
        ld["abstract"] = clip(row["abstract"], 500)
    L += json_ld(ld)

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
    if ctx.get("supervisors"):
        L.append("| Supervisor | " + ", ".join(
            site.link("authors", aid, nm, from_kind=K)
            for aid, nm in ctx["supervisors"]) + " |")
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

    # The mirror of the split on the algorithm pages: what this paper
    # contributes, then what it merely runs.
    for heading, role in (("Methods and tools", "describes"),
                          ("Methods it uses", "uses")):
        rows = [r for r in ctx["algorithms"] if r[3] == role]
        if not rows:
            continue
        L += [f"## {heading}", ""]
        for alg_id, name, descr, _role in rows:
            line = "- " + site.link("algorithms", alg_id, name, from_kind=K)
            if descr:
                line += f": {md_escape(descr)}"
            L.append(line)
        L.append("")

    # Data, split the way the role column splits it: what this paper put into
    # the world, and what it consumed. 'introduces' is the deposit; everything
    # else is use. Dataset names are plain text, not links: there are no dataset
    # pages yet, and inventing a URL here would commit one.
    for heading, roles in (("Data deposited", ("introduces",)),
                           ("Data used", ("uses", "trains-on", "evaluates-on"))):
        rows = [r for r in ctx.get("datasets", []) if r[4] in roles]
        if not rows:
            continue
        L += [f"## {heading}", ""]
        for name, kind, version, addrs, _role in rows:
            line = f"- **{md_escape(name)}**"
            if version:
                line += f" — {md_escape(version)}"
            else:
                # The honest state for a paper that names a dataset without
                # saying which version it ran on.
                line += " <small>(version not stated)</small>"
            if addrs:
                shown = ", ".join(
                    f"[{md_escape(acc)}]({url})" + (" <small>(provenance)</small>" if prov else "")
                    for _repo, acc, url, prov in addrs[:12])
                line += f" · {shown}"
                if len(addrs) > 12:
                    line += f" <small>and {len(addrs) - 12} more</small>"
            elif version:
                # Only claim this when a version IS named. Addresses hang off
                # the version, so an unknown version means unknown addresses,
                # not absent ones: saying "no public address" under a paper that
                # merely failed to say which nine-species it used would be a
                # statement about the benchmark, and a false one.
                line += " <small>· no public address</small>"
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
    # A supervisor recorded via thesis_supervisor may have no catalogued paper of
    # their own, in which case "0 papers" reads like a data error rather than a
    # fact about the person.
    if n:
        sub = f"{n} paper{'s' if n != 1 else ''} in the catalog"
    elif ctx.get("supervised"):
        k = len(ctx["supervised"])
        sub = f"supervised {k} thesis{'es' if k != 1 else ''} in the catalog"
    else:
        sub = "no catalogued papers"
    if ctx["countries"]:
        sub += " · " + ", ".join(ctx["countries"])
    insts = [nm for _iid, nm, _d in ctx["affiliations"]]
    a_desc = [f"{row['display_name']}: {sub}"]
    if insts:
        a_desc.append(", ".join(insts[:2]))
    if ctx["algorithms"]:
        a_desc.append("Works on " + ", ".join(
            nm for _i, nm in list(ctx["algorithms"])[:3]))
    L += front_matter(row["display_name"], sub, clip(" · ".join(a_desc), 250))

    ld = {"@context": "https://schema.org", "@type": "Person",
          "name": row["display_name"]}
    if insts:
        ld["affiliation"] = [{"@type": "Organization", "name": nm} for nm in insts]
    same = [u for u in (
        f"https://orcid.org/{row['orcid']}" if row["orcid"] else None,
        f"https://openalex.org/{row['openalex_id']}" if row["openalex_id"] else None,
    ) if u]
    if same:
        ld["sameAs"] = same
    L += json_ld(ld)

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
    # ORCID first: it is the canonical persistent identifier and the one most
    # readers will want. All of these carry no contact details, unlike the email
    # address that used to sit here.
    ids = []
    if row["orcid"]:
        ids.append(f"[ORCID {row['orcid']}](https://orcid.org/{row['orcid']})")
    if row["scholar_id"]:
        ids.append(f"[Google Scholar](https://scholar.google.com/citations?user={row['scholar_id']})")
    if row["openalex_id"]:
        ids.append(f"[OpenAlex](https://openalex.org/{row['openalex_id']})")
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

    if ctx.get("supervisor"):
        L += ["## Thesis supervisor", ""]
        for aid, nm, pub_id, title in ctx["supervisor"]:
            L.append(f"- {site.link('authors', aid, nm, from_kind=K)}, for "
                     + site.link("publications", pub_id, title, from_kind=K))
        L.append("")

    if ctx.get("supervised"):
        n = len(ctx["supervised"])
        L += [f"## Theses supervised ({n})", ""]
        for pub_id, title, date, student in ctx["supervised"]:
            L.append(f"- {site.link('publications', pub_id, title, from_kind=K)}"
                     f" ({str(date)[:4]}), by {md_escape(student)}")
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


METRIC_LABEL = {"precision": "Precision", "recall": "Recall", "auc": "AUC",
                "precision@cov1": "Precision at coverage 1",
                "ptm-precision": "PTM precision", "ptm-recall": "PTM recall",
                "accuracy": "Accuracy", "accuracy-filtered": "Filtered accuracy",
                "coverage": "Coverage", "positional-accuracy": "Positional accuracy"}


def comparison_table(site: Site, c: dict, from_kind: str) -> list[str]:
    """One printed table, STANDARDISED: methods down the side, the measure and
    then the species across, every value on 0-1.

    Built from paper_comparison_result alone, never from the printed header,
    so a table the paper itself mis-typeset (DiffNovo's Table 1) comes out as
    clean as any other. Bold is the best value in a column and underline the
    runner-up where a column has three or more: OUR ranking, the same rule the
    review page applies, whatever the paper marked.
    """
    def a(kind, eid, label):
        href = site.href(kind, eid, from_kind=from_kind)
        t = html.escape(label)
        return f'<a href="{href}">{t}</a>' if href else t

    # A SECOND METRIC IN ONE PRINTED CELL is not a column of the table. DiffNovo
    # prints PepNet's Plasma precision as '0.491 / 0.725*', the starred number
    # being "the positional accuracy reported in [10]". Given a row and a
    # column of its own it read as a misaligned table -- a mostly empty
    # 'PepNet · quoted' row under a column nobody else fills. The cell's own
    # value stays in the grid; the extra ones move to a note under the table
    # that quotes the printed cell and the paper's footnote. The data are
    # unchanged; this is only how the page shows them.
    first_part = {}
    for x in c["results"]:
        key = (x["row_index"], x["col_index"])
        if key not in first_part or x["part_index"] < first_part[key]["part_index"]:
            first_part[key] = x
    def extra(x):
        f = first_part[(x["row_index"], x["col_index"])]
        return x["part_index"] > 0 and (x["metric"], x["level"]) != (f["metric"], f["level"])
    extras = [x for x in c["results"] if extra(x)]
    res = [x for x in c["results"] if not extra(x)]
    rows, cols = [], []
    first_sub: dict[str, int] = {}
    col_pos: dict[tuple, tuple] = {}
    for x in res:
        rk = (x["algorithm_id"], x["algorithm"], x["variant_printed"] or "", x["basis"])
        ck = (x["subset_canonical"] or "", x["metric"], x["level"])
        if rk not in rows:
            rows.append(rk)
        if ck not in cols:
            cols.append(ck)
        first_sub.setdefault(ck[0], len(first_sub))
        pos = (x["col_index"], x["row_index"])
        col_pos[ck] = min(col_pos.get(ck, pos), pos)
    # MEASURE FIRST, THEN SPECIES. Columns are grouped by what they measure
    # (amino-acid recall, peptide precision, ...), so every column of one
    # measure sits together under one merged header, and within a measure the
    # species keep the paper's order. Measures follow where each first appears
    # in the printed table. Grouping by species first, as the tables used to,
    # split one measure across the width of the table.
    first_meas: dict[tuple, tuple] = {}
    for ck in cols:
        m = (ck[1], ck[2])
        first_meas[m] = min(first_meas.get(m, col_pos[ck]), col_pos[ck])
    cols.sort(key=lambda ck: (first_meas[(ck[1], ck[2])], first_sub[ck[0]], col_pos[ck]))
    val = {}
    for x in res:
        rk = (x["algorithm_id"], x["algorithm"], x["variant_printed"] or "", x["basis"])
        ck = (x["subset_canonical"] or "", x["metric"], x["level"])
        val[(rk, ck)] = x
    # Our ranking, per column.
    mark = {}
    for ck in cols:
        vs = sorted({round(val[(rk, ck)]["value"], 6) for rk in rows if (rk, ck) in val},
                    reverse=True)
        n = sum(1 for rk in rows if (rk, ck) in val)
        for rk in rows:
            if (rk, ck) not in val or n < 2:
                continue
            v = round(val[(rk, ck)]["value"], 6)
            if v == vs[0]:
                mark[(rk, ck)] = "best"
            elif n >= 3 and len(vs) > 1 and v == vs[1]:
                mark[(rk, ck)] = "second"

    ds = (a("datasets", c["dataset_id"], c["dataset"]) if c["dataset_id"]
          else html.escape(c["dataset_printed"] or "dataset not stated"))
    if c["dataset_version"]:
        ds += f'<br><small>{html.escape(c["dataset_version"])}</small>'
    subsets = [ck[0] for ck in cols]
    two_rows = any(subsets)

    def measure(met: str, lev: str) -> str:
        """'Amino acid recall', 'Peptide precision': the level first."""
        lab = METRIC_LABEL.get(met, met)
        if met.startswith("ptm") or not lev:
            return lab
        lab = lab if lab[:2].isupper() else lab[0].lower() + lab[1:]
        return f"{lev[0].upper()}{lev[1:]} {lab}"
    # A WIDE TABLE IS STACKED. Casanovo's Table 2 prints five measures over nine
    # species, 45 columns, and scrolled for a screen and a half. Past
    # MAX_COLS the columns are split at MEASURE boundaries into tables stacked
    # one above the other, each with the same rows and corner, so one measure
    # is never split across two tables; a single measure wider than MAX_COLS
    # is split on its own. The ranking is per column, so nothing moves.
    MAX_COLS = 12
    groups: list[list[tuple]] = []
    for ck in cols:
        if groups and groups[-1][-1][1:] == ck[1:]:
            groups[-1].append(ck)
        else:
            groups.append([ck])
    chunks: list[list[tuple]] = []
    for g in groups:
        for k0 in range(0, len(g), MAX_COLS):
            piece = g[k0:k0 + MAX_COLS]
            if chunks and len(chunks[-1]) + len(piece) <= MAX_COLS and len(cols) > MAX_COLS:
                chunks[-1] += piece
            elif chunks and len(cols) <= MAX_COLS:
                chunks[-1] += piece
            else:
                chunks.append(list(piece))
    out = []
    derived = False
    # Bootstrap's own classes, not new CSS: custom.scss is in the publish's
    # global render key, so styling these in it would force a full render of
    # every page for a section on 22 of them. `table-responsive` lets a wide
    # table (MemNovo's runs to 21 columns) scroll instead of overflowing.
    # THE BASIS IS NOT A COLUMN. It is our reading of the paper's prose, not
    # part of the printed table, and as a column of its own it sat there with
    # no header, mostly reading 'unclear'. Where the paper does say how a
    # method was run, the word follows the method's name and the sentence
    # that says so is its tooltip; where it does not, nothing is shown. Two
    # rows of one method on different bases (CrossNovo's dagger-Casanovo,
    # retrained, beside Casanovo, quoted) stay told apart that way.
    cue_of = {}
    for x in res:
        rk = (x["algorithm_id"], x["algorithm"], x["variant_printed"] or "", x["basis"])
        if x.get("basis_cue") and rk not in cue_of:
            cue_of[rk] = x["basis_cue"]

    def row_head(rk) -> str:
        aid, name, variant, basis = rk
        cell = a("algorithms", aid, name)
        if variant:
            cell += f" <small>{html.escape(variant)}</small>"
        # A BASIS IS ABOUT A BASELINE: how the paper got a number it did not
        # produce with its own method. On the paper's own method it said
        # nothing true -- 'LIPNovo · retrained' quoted a sentence about
        # retraining Casanovo -- so it is shown on baselines only.
        own_row = any(x["is_self"] for x in res
                      if (x["algorithm_id"], x["algorithm"], x["variant_printed"] or "",
                          x["basis"]) == rk)
        if basis and basis != "unclear" and not own_row:
            tip = cue_of.get(rk, "")
            cell += (f' <small class="text-muted" title="{html.escape(tip, quote=True)}">'
                     f"&middot; <em>{html.escape(basis)}</em></small>")
        return cell

    for chunk in chunks:
        sub_c = [ck[0] for ck in chunk]
        two = any(sub_c)
        # Bootstrap's own classes, not new CSS: custom.scss is in the publish's
        # global render key, so styling these in it would force a full render
        # of every page for a section on a few dozen of them.
        out += ['<div class="table-responsive">',
                '<table class="table table-sm table-hover comparison" '
                'style="font-size:0.85em; width:auto">', "<thead>"]
        # Top row: the measure, one merged cell over all its adjacent columns.
        out.append(f'<tr><th rowspan="2">{ds}</th>' if two else f'<tr><th>{ds}</th>')
        i = 0
        while i < len(chunk):
            j = i
            while j + 1 < len(chunk) and chunk[j + 1][1:] == chunk[i][1:]:
                j += 1
            out.append(f'<th colspan="{j - i + 1}" style="text-align:center">'
                       f'{html.escape(measure(chunk[i][1], chunk[i][2]))}</th>')
            i = j + 1
        out.append("</tr>")
        # Second row: the species or test set under each measure.
        if two:
            out.append("<tr>" + "".join(f"<th><small>{html.escape(sub)}</small></th>"
                                        for sub in sub_c) + "</tr>")
        out.append("</thead><tbody>")
        for rk in rows:
            # A row with nothing in this part of a stacked table is left out of
            # it, rather than printed empty.
            if not any((rk, ck) in val for ck in chunk):
                continue
            # nowrap: in a wide table the browser otherwise breaks a method
            # name mid-word ('DeepNov o') to save a column's width.
            out.append(f'<tr><th style="white-space:nowrap">{row_head(rk)}</th>')
            for ck in chunk:
                x = val.get((rk, ck))
                if not x:
                    out.append("<td></td>")
                    continue
                # THE PRINTED PRECISION: '0.530' stays '0.530', not '0.53'. A
                # percentage table moved to 0-1 gains two decimals.
                nums = re.findall(r"\d+(?:\.(\d*))?", x["text_printed"] or "")
                dec = len(nums[x["part_index"]]) if x["part_index"] < len(nums) else 3
                dec += 2 if c["unit_printed"] == "0-100" else 0
                t = f"{x['value']:.{max(dec, 1)}f}"
                if x["derived_from"]:
                    t += "&#8225;"
                    derived = True
                m = mark.get((rk, ck))
                t = f"<strong>{t}</strong>" if m == "best" else f"<u>{t}</u>" if m == "second" else t
                out.append(f"<td>{t}</td>")
            out.append("</tr>")
        out += ["</tbody></table>", "</div>"]
    notes = []
    if derived:
        notes.append("&#8225; Not printed in the paper: computed from the "
                     "differences it prints, as described under the table.")
    seen_cells = set()
    for x in extras:
        key = (x["row_index"], x["col_index"])
        if key in seen_cells:
            continue
        seen_cells.add(key)
        f = first_part[key]
        printed = re.sub(r"\s+", " ", x["text_printed"] or "").strip()
        marks = set(re.findall(r"[*+\u2020\u2021\u00a7]", printed))
        foot = " ".join(sent for sent in re.split(r"(?<=\.)\s+", c.get("footnote") or "")
                        if marks & set(sent))
        where = ", ".join(b for b in (x["algorithm"], x["subset_canonical"]) if b)
        notes.append(f"{html.escape(where)}: the printed cell reads "
                     f"&ldquo;{html.escape(printed)}&rdquo;, and only "
                     f"{html.escape(f['text_printed'].split('/')[0].strip())} is in the table."
                     + (f" The paper&rsquo;s note: &ldquo;{html.escape(foot)}&rdquo;" if foot else ""))
    if c["design_note"]:
        notes.append(html.escape(c["design_note"]))
    for n in c["notes"]:
        notes.append("In the paper: " + html.escape(n))
    if notes:
        # The page is MARKDOWN around this HTML, and Pandoc still reads
        # emphasis inside it: the footnote markers in "0.491 / 0.725*" and
        # "* Indicates ..." paired up into italics and vanished. An entity
        # prints the asterisk and means nothing to Markdown.
        out.append('<p class="comparison-notes"><small>'
                   + "<br>".join(notes).replace("*", "&#42;") + "</small></p>")
    return ["", "\n".join(out), ""]


def render_algorithm(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    K = "algorithms"
    L = []
    sub_bits = [b for b in (row["kind"], row["algorithm_family"]) if b]
    g_desc = [row["name"] + (": " + " · ".join(sub_bits) if sub_bits else "")]
    if row["short_description"]:
        g_desc.append(clip(row["short_description"], 170))
    # No JSON-LD here on purpose: these rows span software, reviews, benchmarks
    # and prose workflow descriptions, and no single schema.org type is honest
    # for all of them. A wrong @type is worse than none.
    L += front_matter(row["name"], " · ".join(sub_bits) if sub_bits else None,
                      clip(join_sentences(g_desc), 250))

    if row["short_description"]:
        L += [md_escape(row["short_description"]), ""]

    L += ["| | |", "|---|---|"]
    # Family is emitted separately, below: it is a LINK for the families that
    # have a page, and md_escape would turn the brackets into literal text.
    for label, value in (
        ("Kind", row["kind"]),
        ("Deep learning", None if row["is_deep_learning"] is None
                          else ("yes" if row["is_deep_learning"] else "no")),
        ("Acquisition", row["acquisition_mode"]),
        ("Also known as", row["aliases"]),
    ):
        if value:
            L.append(f"| {label} | {md_escape(str(value))} |")
    # Outside the loop above, which escapes its values: this row's value is a
    # markdown LINK, and md_escape would turn it into literal brackets. 55
    # algorithm pages carry it, and the area page is where the rest of that area
    # lives. ctx has the (id, label) pair because the slug table is keyed by the
    # subdomain's id while the algorithm row only knows its name.
    if row["algorithm_family"]:
        # A family page exists only where the family has two or more methods --
        # for a one-method family the page would be a copy of this one. So the
        # link is conditional and the bare name is the normal fallback, not a
        # failure.
        fam_key = ctx.get("family_key")
        L.append("| Family | " + (
            site.link("families", fam_key, row["algorithm_family"], from_kind=K)
            if fam_key else md_escape(str(row["algorithm_family"]))) + " |")
    if ctx.get("subdomain"):
        L.append("| Application area | "
                 + site.link("subdomains", ctx["subdomain"][0],
                             ctx["subdomain"][1], from_kind=K) + " |")
    elif row["subdomain"]:
        L.append(f"| Application area | {md_escape(str(row['subdomain']))} |")
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

    # WHERE THE WEIGHTS ARE. A benchmark number is only reproducible if the
    # checkpoint behind it can still be downloaded, and one method name covers
    # several models: the version and the training data are part of the
    # identity, not decoration. `archival` says whether the host has a DOI and
    # a preservation commitment, which is the difference between a citation and
    # a link that may rot.
    if ctx.get("checkpoints"):
        L += ["## Checkpoints", ""]
        has_mirror = any(cp["mirror_url"] for cp in ctx["checkpoints"])
        head = "| Version | Trained on | Host | Licence | Size | Checked |"
        rule = "|---|---|---|---|---|---|"
        if has_mirror:
            # Append a column, do not strip the closing pipe first: head[:-1]
            # removed it and produced "| ... | Checked  Backup |" with a missing
            # separator, which markdown renders as one merged cell.
            head += " Backup |"
            rule += "---|"
        L.append(head)
        L.append(rule)
        for cp in ctx["checkpoints"]:
            ver = cp["tool_version"] or cp["label"] or "—"
            size = (f"{cp['size_bytes'] / 1e9:.1f} GB" if cp["size_bytes"] and cp["size_bytes"] >= 1e9
                    else f"{cp['size_bytes'] / 1e6:.0f} MB" if cp["size_bytes"] else "—")
            host = f"[{md_escape(cp['host'])}]({cp['url']})"
            if cp["archival"]:
                host += " <small>archival</small>"
            # The date says WHICH question was answered when: `verified` carries
            # the day the bytes were hashed, everything else the day the link
            # was last probed. Printing one date for both would conflate "we
            # have this file" with "this link answered".
            state = cp["status"] or "unchecked"
            if state not in ("live", "verified"):
                state = f"**{state}**"
            stamp = cp["verified_at"] if cp["status"] == "verified" else cp["last_checked"]
            if stamp:
                state += f" <small>{stamp}</small>"
            # `line`, NOT `row`: `row` is render_algorithm's own parameter, and
            # shadowing it with a string here broke every later use of it with
            # "string indices must be integers".
            line = (f"| {md_escape(ver)} | {md_escape(cp['trained_on'] or '—')} | {host} "
                    f"| {md_escape(cp['licence'] or 'not stated')} | {size} | {state} |")
            if has_mirror:
                # The ORIGINAL stays the primary link: a mirror is a fallback,
                # and sending readers to a copy by default would hide the fact
                # that the authors published it somewhere.
                line += (f" [copy]({cp['mirror_url']}) |" if cp["mirror_url"] else " — |")
            L.append(line)
        L.append("")
        if any(not cp["archival"] for cp in ctx["checkpoints"]):
            note = ("A host marked *archival* has a DOI and keeps what it is given. "
                    "The others can move or disappear, which is why they are checked "
                    "rather than merely listed. **verified** means the bytes were "
                    "fetched and hashed on the date shown; **live** means only that "
                    "the host answered when last asked.")
            if has_mirror:
                note += (" Where a *copy* is linked, it is a backup of someone "
                         "else's weights kept in case the original link goes stale; "
                         "the original is the link to cite and to prefer.")
            L += [note, ""]

    # Where this method stands on the two public benchmarks, when it was run on
    # them: one line each, carrying the numbers that need no context to read,
    # and a link to the section that supplies the context.
    if ctx["bench"] or ctx["proteobench"]:
        L += ["## Benchmarks", ""]
        b = ctx["bench"]
        if b:
            L.append(
                "- **denovo_benchmarks**: median peptide-level average precision "
                f"**{b['median_ap']:.3f}** over "
                + (f"[{b['n_datasets']} datasets]({site.href('datasets', ctx['bench_ds_id'], from_kind=K)}"
                   "#denovo-benchmarks-datasets)" if ctx.get("bench_ds_id")
                   else f"{b['n_datasets']} datasets")
                + ", median "
                f"rank **{b['median_rank']:g}** of {b['of']}"
                + (f" (version {md_escape(str(b['version']))})" if b["version"] else "")
                + ".")
        pb = ctx["proteobench"]
        if pb and "peptide" in pb:
            prec, auc, cov = pb["peptide"]
            bits = [f"peptide-level AUC **{auc:.3f}**",
                    f"precision {prec:.3f} at {cov:.0%} coverage"]
            if "aa" in pb:
                bits.append(f"amino-acid AUC {pb['aa'][1]:.3f}")
            detail = ", ".join(x for x in (
                f"version {md_escape(str(pb['version']))}" if pb["version"] else None,
                md_escape(pb["decoding"]) if pb["decoding"] else None,
                f"submitted {pb['submitted']}" if pb["submitted"] else None) if x)
            # ON WHICH DATA: ProteoBench scores one dataset, its own selection
            # of the nine-species benchmark, which the catalog holds as a
            # version of that dataset; the line links straight to it.
            nine = (f"on the [nine-species benchmark]({site.href('datasets', ctx['nine_ds_id'], from_kind=K)}), "
                    f"[ProteoBench selection]({site.href('datasets', ctx['nine_ds_id'], from_kind=K)}"
                    "#proteobench-selection): " if ctx.get("nine_ds_id") else "")
            L.append("- **ProteoBench**, " + nine + "; ".join(bits)
                     + (f" ({detail})" if detail else "") + ".")
        L += ["", "Both are mass-based matches on the tool's most recent run. "
              f"[What these numbers mean]({site.home('benchmarks')}).", ""]

    # WHAT THIS METHOD'S OWN PAPERS REPORT, standardised from the comparison
    # tables mined out of them. After the benchmarks on purpose: the reader
    # meets the independently run numbers first.
    if ctx.get("comparisons"):
        n = len(ctx["comparisons"])
        L += [f"## Reported comparisons ({n})" if n > 1 else "## Reported comparison", "",
              "The comparison table" + ("s" if n > 1 else "") + " this method's own "
              "papers print, standardised: every value on a 0-1 scale, methods "
              "down the side, the measure and then the species across. These are numbers papers "
              "report **about themselves and their baselines**. They are not a "
              "leaderboard, and they do not compare across tables: each was "
              "produced by a different group, on the dataset named in its "
              "corner, with each baseline either retrained, run from released "
              "weights or quoted from another paper. Where the paper says which, "
              "it follows the method's name (hover it for the sentence); most "
              "papers do not say. "
              "**Bold** is the best value in a column and underline the "
              "runner-up, our ranking rather than the paper's own marks.", ""]
        for c in ctx["comparisons"]:
            head = (f"### {md_escape(c['table_label'])}"
                    + (f" ({md_escape(c['part'])})" if c["part"] else ""))
            L += [head, "",
                  f"{site.link('publications', c['publication_id'], c['title'], from_kind=K)}, "
                  f"page {c['pdf_page']}: *{md_escape(c['caption'])}*", ""]
            if c.get("also_in"):
                L += ["The same table is also printed in "
                      + ", ".join(site.link("publications", t["publication_id"],
                                            t["title"], from_kind=K)
                                  + f" ({md_escape(t['table_label'])}, page {t['pdf_page']})"
                                  for t in c["also_in"]) + ".", ""]
            L += comparison_table(site, c, K)

    # Two sections, never one: a paper that introduced this method and a paper
    # that ran it on a snake venom are not the same claim, and PEAKS's list of
    # 21 was 18 of the latter.
    def paper_lines(rows: list[tuple]) -> None:
        for pub_id, title, date, journal, ptype, _role in rows:
            line = f"- {site.link('publications', pub_id, title, from_kind=K)}"
            meta = [m for m in (str(date)[:4] if date else None, journal, ptype) if m]
            if meta:
                line += f" ({md_escape(', '.join(meta))})"
            L.append(line)
        L.append("")

    if ctx["described"]:
        n = len(ctx["described"])
        L += [f"## Paper{'s' if n != 1 else ''} describing it"
              + (f" ({n})" if n > 1 else ""), ""]
        paper_lines(ctx["described"])
    if ctx["used"]:
        n = len(ctx["used"])
        L += [f"## Paper{'s' if n != 1 else ''} using it"
              + (f" ({n})" if n > 1 else ""), "",
              ("Applications and evaluations that ran this method. They are not "
               "counted among its authors below." if n != 1 else
               "An application or evaluation that ran this method. It is not "
               "counted among its authors below."), ""]
        paper_lines(ctx["used"])

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
    # The page's mtime is the date of the paper that DESCRIBES the method, not
    # of the most recent paper to use it: the mtime is what the sitemap reports
    # as <lastmod>, and a method does not change because someone ran it.
    earliest = min((p[2] for p in ctx["described"] if p[2]), default=None)
    return "\n".join(L) + "\n", date_to_mtime(earliest)


def render_institution(site: Site, name: str, ctx: dict) -> tuple[str, float]:
    K = "institutions"
    L = []
    sub = []
    if ctx["places"]:
        sub.append(", ".join(ctx["places"]))
    sub.append(f"{len(ctx['authors'])} author{'s' if len(ctx['authors']) != 1 else ''}")
    i_desc = (f"{name}"
              + (f" ({', '.join(ctx['places'])})" if ctx["places"] else "")
              + f": {len(ctx['authors'])} author"
              + ("s" if len(ctx["authors"]) != 1 else "")
              + f" and {len(ctx['pubs'])} paper"
              + ("s" if len(ctx["pubs"]) != 1 else "")
              + " in the de novo peptide sequencing catalog.")
    L += front_matter(name, " · ".join(sub), clip(i_desc, 250))
    ld = {"@context": "https://schema.org", "@type": "Organization", "name": name}
    if ctx["places"]:
        ld["address"] = ", ".join(ctx["places"])
    L += json_ld(ld)

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
    n_v = len(ctx["pubs"])
    L += front_matter(name, f"{n_v} paper{'s' if n_v != 1 else ''} in the catalog",
                      clip(f"{n_v} de novo peptide sequencing paper"
                           f"{'s' if n_v != 1 else ''} published in {name}, "
                           f"catalogued with authors, methods and citation counts.", 250))
    L += json_ld({"@context": "https://schema.org", "@type": "Periodical", "name": name})
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


def render_dataset(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    """One dataset: its versions, where each version lives, and who used it.

    The page the Every-dataset table sends a reader to. It exists because
    "nine-species" names four different objects and no other page can say so:
    the version list with its addresses IS the answer to which spectra a number
    was computed over.

    Addresses are per VERSION, not per dataset, because that is the grain they
    have. Provenance addresses are marked as such rather than listed alongside
    the real ones: for the nine-species benchmark those nine PRIDE submissions
    are where the spectra came from, not where the benchmark is.
    """
    K = "datasets"
    L = []
    bits = [row["kind"]]
    if row["acquisition_mode"]:
        bits.append(row["acquisition_mode"])
    n_pubs = len({r["pub_id"] for r in ctx["pubs"]})
    bits.append(f"{len(ctx['versions'])} version{'s' if len(ctx['versions']) != 1 else ''}")
    if n_pubs:
        bits.append(f"{n_pubs} paper{'s' if n_pubs != 1 else ''}")
    desc = row["short_description"] or f"{row['name']}, a {row['kind']} in the de novo peptide sequencing catalog."
    L += front_matter(row["name"], " · ".join(bits), clip(desc, 250))

    ld = {"@context": "https://schema.org", "@type": "Dataset",
          "name": row["name"], "description": clip(desc, 500)}
    if row["homepage"]:
        ld["url"] = row["homepage"]
    accs = [a["accession"] for v in ctx["versions"] for a in ctx["addrs"].get(v["id"], [])]
    if accs:
        ld["identifier"] = accs[:10]
    L += json_ld(ld)

    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Kind | {md_escape(row['kind'])} |")
    if row["acquisition_mode"]:
        L.append(f"| Acquisition | {md_escape(row['acquisition_mode'])} |")
    if row["organisms"]:
        L.append(f"| Organisms | {md_escape(row['organisms'])} |")
    if row["homepage"]:
        L.append(f"| Home | <{row['homepage']}> |")
    L.append("")
    if row["short_description"]:
        L += [italicise_de_novo(md_escape(row["short_description"])), ""]

    # ---- versions, each with where it lives
    L += ["## Versions", ""]
    for v in ctx["versions"]:
        head = f"### {md_escape(v['version'])}"
        L += [head, ""]
        if v["description"]:
            L += [italicise_de_novo(md_escape(v["description"])), ""]
        facts = []
        for label, key in (("spectra", "n_spectra"), ("train", "n_train"),
                           ("validation", "n_validation"), ("test", "n_test")):
            if v[key]:
                facts.append(f"{label} {v[key]:,}")
        if v["released"]:
            facts.append(f"released {str(v['released'])[:10]}")
        if v["introduced_by"]:
            intro = ctx["pub_titles"].get(v["introduced_by"])
            if intro:
                facts.append("introduced by " + site.link("publications", v["introduced_by"],
                                                          intro, from_kind=K))
        if facts:
            L += [" · ".join(facts), ""]
        direct = [a for a in ctx["addrs"].get(v["id"], []) if not a["is_provenance"]]
        prov = [a for a in ctx["addrs"].get(v["id"], []) if a["is_provenance"]]
        if direct:
            L += ["Where it lives:", ""]
            for a in direct:
                line = f"- **{md_escape(a['repository'])}** · [{md_escape(a['accession'])}]({a['url']})"
                if a["part"]:
                    line += f" <small>{md_escape(a['part'])}</small>"
                L.append(line)
            L.append("")
        if prov:
            # Named for what they are. These are other people's studies whose
            # spectra were re-curated, not copies of this dataset.
            L += [f"Assembled from {len(prov)} third-party submission"
                  f"{'s' if len(prov) != 1 else ''}:", ""]
            for a in prov:
                line = f"- [{md_escape(a['accession'])}]({a['url']})"
                if a["part"]:
                    line += f" — {md_escape(a['part'])}"
                L.append(line)
            L.append("")
        if not direct and not prov:
            L += ["No public address: this version is named in the literature but "
                  "cannot be downloaded.", ""]

    # ---- denovo_benchmarks' evaluation sets, on the page of its own deposit.
    # The method pages say "over 84 datasets" and link here, so the 84 are
    # nameable rather than a bare count.
    if ctx.get("bench_list"):
        bl = ctx["bench_list"]
        L += [f"## The {len(bl)} datasets denovo_benchmarks evaluates on "
              "{#denovo-benchmarks-datasets}", "",
              "Every dataset the living benchmark scores each tool on, as its "
              "repository names them, with the group used for the heatmap on the "
              f"[main page]({site.home('benchmarks')}) and how many tools have a "
              "result there. Most tools ran on all of them; a partial one is left "
              "out of the cross-dataset ranking.", "",
              "| Dataset | Category | Group | Tools |", "|---|---|---|---:|"]
        for r in bl:
            L.append(f"| {md_escape(r['name'])} | {md_escape(r['category'] or '')} | "
                     f"{md_escape(r['cat_group'] or '')} | {r['n_tools']} |")
        L.append("")

    # ---- papers, split on what they did with it
    for heading, roles, blurb in (
        ("Deposited by", ("introduces",), "produced this data"),
        ("Used by", ("uses", "trains-on", "evaluates-on"), "ran on it"),
    ):
        rows = [r for r in ctx["pubs"] if r["role"] in roles]
        if not rows:
            continue
        L += [f"## {heading} ({len({r['pub_id'] for r in rows})})", ""]
        if heading == "Deposited by" and len({r["pub_id"] for r in rows}) > 1:
            # TWO DEPOSITORS HAVE TWO LEGITIMATE SHAPES and the note has to say
            # which one this is. Either one work was published twice, a
            # preprint and its version of record both introducing the same
            # version, or two different works each introduced a DIFFERENT
            # version of the dataset. The seven-species benchmark is the second
            # kind: DeepNovo introduced the original and NovoBench its fixed
            # split, seven years apart. Printing the preprint sentence there
            # was a false statement on a published page.
            by_version: dict = {}
            for r in rows:
                by_version.setdefault(r["version"], set()).add(r["pub_id"])
            if any(len(v) > 1 for v in by_version.values()):
                L += ["More than one paper here means one piece of work "
                      "published twice, a preprint and its version of record; "
                      "a deposit itself happens once.", ""]
            else:
                L += ["Each of these introduced a DIFFERENT version, listed "
                      "beside it; a deposit itself happens once.", ""]
        seen = set()
        for r in rows:
            if r["pub_id"] in seen:
                continue
            seen.add(r["pub_id"])
            line = "- " + site.link("publications", r["pub_id"], r["title"], from_kind=K)
            if r["publication_date"]:
                line += f" ({str(r['publication_date'])[:4]})"
            # The TYPE, because a preprint and its version of record share a
            # title and a year: without it the pair renders as two identical
            # lines and reads like duplicated data.
            if r["publication_type"]:
                line += f" <small>{md_escape(r['publication_type'])}</small>"
            vers = sorted({x["version"] for x in rows if x["pub_id"] == r["pub_id"] and x["version"]})
            # A paper that named no version is the finding the catalog exists to
            # record, so say so rather than leaving the line bare.
            line += (f" <small>{md_escape(', '.join(vers))}</small>" if vers
                     else " <small>version not stated</small>")
            L.append(line)
        L.append("")

    # Weights trained on this data. The reason a reader is on this page at all
    # is usually to find out what a number was computed over; the other half of
    # that question is which model produced it.
    if ctx.get("checkpoints"):
        L += [f"## Checkpoints trained on this ({len(ctx['checkpoints'])})", ""]
        L.append("| Method | Checkpoint | Version of this dataset | Size | Get it |")
        L.append("|---|---|---|---|---|")
        for cp in ctx["checkpoints"]:
            ver = cp["tool_version"] or cp["label"] or "—"
            size = (f"{cp['size_bytes'] / 1e9:.1f} GB" if cp["size_bytes"] and cp["size_bytes"] >= 1e9
                    else f"{cp['size_bytes'] / 1e6:.0f} MB" if cp["size_bytes"] else "—")
            # "not stated" is the honest cell: these records name the dataset
            # and not which of its versions, which is the same ambiguity the
            # Versions list above exists to expose.
            dsver = md_escape(cp["version"]) if cp["version"] else "<small>not stated</small>"
            get = f"[{md_escape(cp['host'])}]({cp['url']})"
            if cp["mirror_url"]:
                get += f" · [copy]({cp['mirror_url']})"
            if cp["status"] == "gated":
                get += " <small>**gated**</small>"
            L.append(f"| {site.link('algorithms', cp['alg_id'], cp['method'], from_kind=K)} "
                     f"| {md_escape(ver)} | {dsver} | {size} | {get} |")
        L.append("")
        L += ["Each link rests on stated evidence rather than a text match:", ""]
        for cp in ctx["checkpoints"]:
            L.append(f"- **{md_escape(cp['method'])}** "
                     f"{md_escape(cp['tool_version'] or cp['label'] or '')}: "
                     f"{md_escape(cp['evidence'])}")
        L.append("")

    if ctx["methods"]:
        L += [f"## Methods on these papers ({len(ctx['methods'])})", ""]
        for m in ctx["methods"]:
            L.append(f"- " + site.link("algorithms", m["alg_id"], m["name"], from_kind=K)
                     + f" <small>{md_escape(m['kind'])}</small>")
        L += ["", "Taken from the describing links only, so a paper that merely ran a "
              "tool on this data does not make that tool a method of it.", ""]

    L += site.seen_in(["datasets"])
    dates = [str(v["released"])[:10] for v in ctx["versions"] if v["released"]]
    return "\n".join(L) + "\n", date_to_mtime(max(dates) if dates else None)


def render_subdomain(site: Site, row: dict, ctx: dict) -> tuple[str, float]:
    """One application area: what de novo sequencing is used for here.

    The only generated page whose subject is not a row someone can cite. It
    exists because the Application-areas swim lane and the Sankey both aggregate
    on this axis and had nowhere to send a reader, and because the question
    "what has de novo sequencing actually been used for in venomics" is answered
    by a list this page can assemble and no other page can.
    """
    K = "subdomains"
    L = []
    n_m, n_p = len(ctx["methods"]), len(ctx["pubs"])
    span = ctx["span"]
    sub = f"{n_m} workflow{'s' if n_m != 1 else ''}"
    if span:
        sub += f" · {span[0][:4]}–{span[1][:4]}" if span[0][:4] != span[1][:4] else f" · {span[0][:4]}"
    L += front_matter(row["label"], sub,
                      clip(join_sentences([
                          f"{row['label']}: {row['blurb']}" if row["blurb"] else row["label"],
                          f"{n_m} catalogued workflow{'s' if n_m != 1 else ''} "
                          f"and {n_p} paper{'s' if n_p != 1 else ''}."]), 250))
    if row["blurb"]:
        L += [italicise_de_novo(md_escape(row["blurb"])), ""]

    L += ["| | |", "|---|---|",
          f"| Workflows | {n_m} |",
          f"| Papers | {n_p} |",
          f"| Authors | {len(ctx['authors'])} |"]
    if span:
        L.append(f"| Active | {span[0]} to {span[1]} |")
    L.append("")

    L += [f"## Workflow{'s' if n_m != 1 else ''} ({n_m})", ""]
    for alg_id, name, descr, date in ctx["methods"]:
        line = "- " + site.link("algorithms", alg_id, name, from_kind=K)
        if date:
            line += f" ({str(date)[:4]})"
        if descr:
            line += f": {md_escape(descr)}"
        L.append(line)
    L.append("")

    if ctx["pubs"]:
        L += [f"## Paper{'s' if n_p != 1 else ''} ({n_p})", ""]
        for pub_id, title, date, journal, ptype in ctx["pubs"]:
            meta = [m for m in (str(date)[:4] if date else None, journal, ptype) if m]
            L.append(f"- {site.link('publications', pub_id, title, from_kind=K)}"
                     + (f" ({md_escape(', '.join(meta))})" if meta else ""))
        L.append("")

    if ctx["tools"]:
        L += ["## Sequencing tools these papers used", "",
              # The tools are the 'uses' side of publication_algorithm, which is
              # exactly what this page is for: the workflows above are what the
              # papers contribute, the tools below are what they ran.
              ", ".join(site.link("algorithms", a_id, name, from_kind=K)
                        for a_id, name in ctx["tools"]), ""]

    if ctx["authors"]:
        L += [f"## Authors ({len(ctx['authors'])})", "",
              ", ".join(site.link("authors", a_id, name, from_kind=K)
                        for a_id, name in ctx["authors"]), ""]

    if ctx["countries"]:
        L += ["## Where the work happened", "",
              ", ".join(md_escape(c) for c in ctx["countries"]), ""]

    L += site.seen_in(["applications", "browse-papers"])
    earliest = span[0] if span else None
    return "\n".join(L) + "\n", date_to_mtime(earliest)


def render_family(site: Site, fam: dict, ctx: dict) -> tuple[str, float]:
    """One architecture family: the methods that share it, and who built them.

    Only families with two or more methods get a page (the HAVING clause in
    slugs.py). A single-method family has nothing to aggregate: its papers, its
    authors and its dates are that one method's, so the page would be a copy of
    the method's own. The swim lane still links every lane label, sending a
    singleton lane straight to its method instead.

    Everything here comes through the 'describes' links. A family is a claim
    about how a method works, so the papers that merely ran it belong on the
    method's page, not here.
    """
    K = "families"
    L = []
    methods = ctx["methods"]
    n_m, n_p = len(methods), len(ctx["pubs"])
    span = ctx["span"]
    sub = f"{n_m} methods"
    if span:
        years = (span[0][:4], span[1][:4])
        sub += f" · {years[0]}–{years[1]}" if years[0] != years[1] else f" · {years[0]}"
    first = methods[0]
    blurb = ctx.get("blurb")
    # Plain "de novo" here, not *de novo*: this string becomes a <meta
    # name="description">, where asterisks would render literally. The blurb is
    # written that way in the database for the same reason the subdomain blurbs
    # are, and italicised below where it reaches the page body.
    # The blurb IS the description where there is one: it says what the family
    # is, which is what a search snippet should say, and appending a count to it
    # only pushed the last clause past the 250-char clip on 16 of the 24.
    L += front_matter(
        fam["name"], sub,
        clip(f"{fam['name']}: {blurb}" if blurb else join_sentences([
            f"{fam['name']}: an architecture family in the de novo peptide "
            f"sequencing catalog, shared by {n_m} methods",
            f"{n_m} catalogued methods, described in {n_p} "
            f"paper{'s' if n_p != 1 else ''}."]), 250))

    if blurb:
        L += [italicise_de_novo(md_escape(blurb)), ""]

    # With a blurb the reader already knows what the family is, so the generated
    # sentence only has to place it in time; without one it also has to say what
    # the page is.
    lead = [] if blurb else [f"An architecture family, shared by {n_m} catalogued methods."]
    if first["first_pub"]:
        earliest_link = site.link("algorithms", first["id"], first["name"], from_kind=K)
        lead.append(
            (f"The earliest of its {n_m} methods is {earliest_link} "
             f"({str(first['first_pub'])[:4]})" if blurb else
             f"The earliest is {earliest_link} ({str(first['first_pub'])[:4]})")
            + (f"; {n_m - 1} more have followed." if n_m > 2 else "."))
    if lead:
        L += [" ".join(lead), ""]

    dl = [m for m in methods if m["dl"] == 1]
    kinds = Counter(m["kind"] for m in methods if m["kind"])
    modes = Counter(m["mode"] for m in methods if m["mode"])

    def tally(counter: Counter) -> str:
        return ", ".join(f"{md_escape(k)} ({v})" if v > 1 else md_escape(k)
                         for k, v in sorted(counter.items(), key=lambda t: (-t[1], t[0])))

    L += ["| | |", "|---|---|",
          f"| Methods | {n_m} |",
          f"| Papers describing them | {n_p} |",
          f"| Authors | {len(ctx['authors'])} |"]
    if span:
        L.append(f"| Active | {span[0]} to {span[1]} |")
    if dl:
        L.append(f"| Deep learning | {len(dl)} of {n_m} |")
    if kinds:
        L.append(f"| Kinds | {tally(kinds)} |")
    if modes:
        L.append(f"| Acquisition | {tally(modes)} |")
    L.append("")

    L += [f"## Methods ({n_m})", "",
          "Oldest first, by the paper that describes each one.", ""]
    for m in methods:
        line = "- " + site.link("algorithms", m["id"], m["name"], from_kind=K)
        if m["first_pub"]:
            line += f" ({str(m['first_pub'])[:4]})"
        if m["descr"]:
            line += f": {md_escape(m['descr'])}"
        L.append(line)
    L.append("")

    # The family's own benchmark record, which is the one thing on this page
    # that no method page can show: how the family's methods place against the
    # whole field rather than one against the others.
    scored = [(m, ctx["bench"][m["id"]]) for m in methods if m["id"] in ctx["bench"]]
    if scored:
        best = min(b["median_rank"] for _m, b in scored)
        L += ["## How they score", "",
              f"{len(scored)} of the {n_m} {'has' if len(scored) == 1 else 'have'} been run "
              f"on [denovo_benchmarks]({site.home('benchmarks')}), which ranks "
              f"{scored[0][1]['of']} tools over {scored[0][1]['n_datasets']} datasets. "
              f"The family's best median rank is **{best:g}**.", ""]
        for m, b in sorted(scored, key=lambda t: t[1]["median_rank"]):
            L.append(f"- {site.link('algorithms', m['id'], m['name'], from_kind=K)}: "
                     f"median peptide-level average precision **{b['median_ap']:.3f}**, "
                     f"median rank **{b['median_rank']:g}** of {b['of']}")
        L += ["", "Read these next to the rest of the field, not on their own: "
              f"[what the numbers mean]({site.home('benchmarks')}).", ""]

    if ctx["subdomains"]:
        L += ["## Applied in", "",
              ", ".join(site.link("subdomains", s_id, label, from_kind=K)
                        for s_id, label in ctx["subdomains"]), ""]

    if ctx["pubs"]:
        L += [f"## Paper{'s' if n_p != 1 else ''} describing them ({n_p})", ""]
        for pub_id, title, date, journal, ptype in ctx["pubs"]:
            meta = [x for x in (str(date)[:4] if date else None, journal, ptype) if x]
            L.append(f"- {site.link('publications', pub_id, title, from_kind=K)}"
                     + (f" ({md_escape(', '.join(meta))})" if meta else ""))
        L.append("")

    if ctx["authors"]:
        L += [f"## Authors ({len(ctx['authors'])})", "",
              ", ".join(site.link("authors", a_id, name, from_kind=K)
                        for a_id, name in ctx["authors"]), ""]

    keys = ["architectures", "browse-papers"]
    if scored:
        keys.insert(1, "benchmarks")
    L += site.seen_in(keys)
    earliest = span[0] if span else None
    return "\n".join(L) + "\n", date_to_mtime(earliest)

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
        "SELECT id, display_name, scholar_id, sciprofiles_id, orcid, openalex_id "
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

    # publication_algorithm.role says what the paper does with the method,
    # 'describes' or 'uses'. It is carried through both directions here: an
    # algorithm page separates the papers that define it from the papers that
    # merely run it, and a publication page says which of its methods it
    # introduces. 165 of 1027 links are 'uses', but they are concentrated: 18 of
    # PEAKS's 21 papers are applications, and listing them as its papers also
    # credited all 107 of their authors as its authors.
    d["pub_algs"] = defaultdict(list)
    d["alg_pubs"] = defaultdict(list)
    for r in q("SELECT pa.publication_id, a.id AS aid, a.name, a.short_description, "
               "p.title, p.publication_date, p.journal, p.publication_type, a.kind, "
               "pa.role "
               "FROM publication_algorithm pa "
               "JOIN algorithm a ON a.id = pa.algorithm_id "
               "JOIN publication p ON p.id = pa.publication_id "
               "ORDER BY pa.publication_id, a.name, a.id"):
        d["pub_algs"][r["publication_id"]].append(
            (r["aid"], r["name"], r["short_description"], r["kind"], r["role"]))
        # role goes LAST: by_date_desc below reads the publication id at index 0
        # and the date at index 2 of every tuple in these buckets.
        d["alg_pubs"][r["aid"]].append(
            (r["publication_id"], r["title"], r["publication_date"],
             r["journal"], r["publication_type"], r["role"]))

    # Checkpoints per algorithm, in the order a reader wants them: archival
    # first, then by tool version. One method name covers several models --
    # Casanovo 4.2.0 was trained on ~2M PSMs from MassIVE-KB v1 + v2.0.15 and
    # 5.2.0-Orbitrap is a different selector -- so the version and the training
    # data are shown, not just a link.
    d["checkpoints"] = defaultdict(list)
    for r in q("SELECT algorithm_id, label, tool_version, trained_on, host, url, "
               "       accession, licence, size_bytes, archival, status, last_checked, "
               "       filename, mirror_url, verified_at "
               "  FROM checkpoint "
               " ORDER BY archival DESC, tool_version, host, id"):
        d["checkpoints"][r["algorithm_id"]].append(r)
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
    # Thesis supervision, both directions. Kept out of publication_author on
    # purpose: a supervisor is not an author, and treating them as one would
    # inflate their publication count and forge a co-authorship edge. This is
    # also the only thing that connects several thesis students to the field at
    # all: 4 of the catalog's 6 authors with zero co-authors are thesis students.
    # Datasets per publication. Grouped in Python rather than by a GROUP_CONCAT
    # because each row carries three independently-nullable things (version,
    # address list, role) and the byline note under 'The Quarto site' is the
    # standing warning about what a trailing ORDER BY does to GROUP_CONCAT.
    #
    # Addresses are fetched per VERSION, not per dataset, so a paper that names
    # the revised benchmark does not get the original's accession printed under
    # it. A version with no address is normal: see 'Datasets, at three grains'.
    addr_of_version = defaultdict(list)
    for r in q("SELECT dataset_version_id, repository, accession, url, is_provenance "
               "FROM dataset_address ORDER BY is_provenance, repository, accession"):
        addr_of_version[r["dataset_version_id"]].append(
            (r["repository"], r["accession"], r["url"], r["is_provenance"]))
    # Per-dataset page data. Versions and addresses are already loaded above for
    # the publication pages; these are the dataset-side views of the same rows.
    d["datasets"] = q("SELECT id, name, short_description, kind, acquisition_mode, "
                      "       organisms, homepage FROM dataset ORDER BY name")
    d["ds_versions"] = defaultdict(list)
    for r in q("SELECT dataset_id, id, version, description, n_spectra, n_train, "
               "       n_validation, n_test, released, introduced_by "
               "  FROM dataset_version ORDER BY dataset_id, id"):
        d["ds_versions"][r["dataset_id"]].append(r)
    d["ds_addrs"] = defaultdict(list)          # version id -> addresses
    for r in q("SELECT dataset_version_id, repository, accession, url, part, is_provenance "
               "  FROM dataset_address ORDER BY is_provenance, repository, accession"):
        d["ds_addrs"][r["dataset_version_id"]].append(r)
    # Papers per dataset, carrying the role so a page can separate the work that
    # DEPOSITED the data from the work that merely ran on it, the same split the
    # algorithm pages make on publication_algorithm.role.
    d["ds_pubs"] = defaultdict(list)
    for r in q("SELECT pd.dataset_id, pd.role, pd.dataset_version_id, p.id AS pub_id, "
               "       p.title, p.publication_date, p.publication_type, dv.version "
               "  FROM publication_dataset pd "
               "  JOIN publication p ON p.id = pd.publication_id "
               "  LEFT JOIN dataset_version dv ON dv.id = pd.dataset_version_id "
               " ORDER BY pd.dataset_id, p.publication_date DESC"):
        d["ds_pubs"][r["dataset_id"]].append(r)
    # Methods reached through those papers, via the DESCRIBING links only: a
    # venomics paper that ran PEAKS on a deposit does not make PEAKS a method
    # of that deposit, the same reasoning as the author->model graph.
    # Checkpoints trained on each dataset. From the CURATED checkpoint_dataset
    # table, never from `trained_on`, which is prose; the evidence column comes
    # along so the page can say why each link exists.
    d["ds_checkpoints"] = defaultdict(list)
    for r in q("SELECT cd.dataset_id, cd.dataset_version_id, cd.evidence, "
               "       a.id AS alg_id, a.name AS method, "
               "       c.label, c.tool_version, c.host, c.url, c.mirror_url, "
               "       c.size_bytes, c.status, dv.version "
               "  FROM checkpoint_dataset cd "
               "  JOIN checkpoint c ON c.id = cd.checkpoint_id "
               "  JOIN algorithm a  ON a.id = c.algorithm_id "
               "  LEFT JOIN dataset_version dv ON dv.id = cd.dataset_version_id "
               " ORDER BY cd.dataset_id, a.name, c.tool_version"):
        d["ds_checkpoints"][r["dataset_id"]].append(r)
    d["ds_methods"] = defaultdict(list)
    for r in q("SELECT DISTINCT pd.dataset_id, a.id AS alg_id, a.name, a.kind "
               "  FROM publication_dataset pd "
               "  JOIN publication_algorithm pa ON pa.publication_id = pd.publication_id "
               "                               AND pa.role = 'describes' "
               "  JOIN algorithm a ON a.id = pa.algorithm_id "
               " ORDER BY pd.dataset_id, a.name"):
        d["ds_methods"][r["dataset_id"]].append(r)
    d["pub_datasets"] = defaultdict(list)       # publication id -> [(name, kind, version, addrs, role)]
    for r in q("SELECT pd.publication_id, pd.role, pd.dataset_version_id, "
               "       ds.name AS ds_name, ds.kind, dv.version "
               "  FROM publication_dataset pd "
               "  JOIN dataset ds ON ds.id = pd.dataset_id "
               "  LEFT JOIN dataset_version dv ON dv.id = pd.dataset_version_id "
               " ORDER BY ds.name, dv.version"):
        d["pub_datasets"][r["publication_id"]].append(
            (r["ds_name"], r["kind"], r["version"],
             addr_of_version.get(r["dataset_version_id"], []), r["role"]))
    d["supervisors_of"] = defaultdict(list)     # publication id -> [(aid, name)]
    d["supervised_by"] = defaultdict(list)      # supervisor id -> [(pub, title, date, student)]
    d["supervisor_for_student"] = defaultdict(list)  # student id -> [(aid, name, pub, title)]
    for r in q("SELECT ts.publication_id, ts.author_id, sup.display_name AS sup_name, "
               "p.title, p.publication_date, "
               "stu.id AS student_id, stu.display_name AS student_name "
               "FROM thesis_supervisor ts "
               "JOIN publication p ON p.id = ts.publication_id "
               "JOIN author_display sup ON sup.id = ts.author_id "
               "JOIN publication_author pa ON pa.publication_id = p.id "
               "JOIN author_display stu ON stu.id = pa.author_id "
               "ORDER BY p.publication_date DESC, ts.author_id, pa.author_order"):
        pair = (r["author_id"], r["sup_name"])
        if pair not in d["supervisors_of"][r["publication_id"]]:
            d["supervisors_of"][r["publication_id"]].append(pair)
        d["supervised_by"][r["author_id"]].append(
            (r["publication_id"], r["title"], r["publication_date"], r["student_name"]))
        d["supervisor_for_student"][r["student_id"]].append(
            (r["author_id"], r["sup_name"], r["publication_id"], r["title"]))

    # Application areas: the rows, and everything that hangs off each one.
    # `subdomain` gives the label and the blurb; the rest is assembled from the
    # workflows tagged with it. Papers come through the workflow rows, so a
    # paper that used de novo sequencing for venomics reaches this page whether
    # or not the paper itself says "venomics" anywhere.
    d["subdomains"] = [dict(r) for r in
                       q("SELECT id, name, label, blurb FROM subdomain ORDER BY id")]
    d["subdomain_by_name"] = {r["name"]: (r["id"], r["label"]) for r in d["subdomains"]}
    d["sub_methods"] = defaultdict(list)
    for r in q("SELECT a.subdomain AS sd, a.id, a.name, a.short_description AS descr,"
               "       MIN(p.publication_date) AS first_pub "
               "FROM algorithm a "
               "LEFT JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "LEFT JOIN publication p ON p.id = pa.publication_id "
               "WHERE COALESCE(a.subdomain, '') <> '' "
               "GROUP BY a.id ORDER BY first_pub, a.name"):
        d["sub_methods"][r["sd"]].append((r["id"], r["name"], r["descr"], r["first_pub"]))

    d["sub_pubs"] = defaultdict(list)
    d["sub_authors"] = defaultdict(list)
    d["sub_tools"] = defaultdict(list)
    d["sub_countries"] = defaultdict(list)
    for r in q("SELECT DISTINCT a.subdomain AS sd, p.id, p.title, p.publication_date,"
               "       p.journal, p.publication_type "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "JOIN publication p ON p.id = pa.publication_id "
               "WHERE COALESCE(a.subdomain, '') <> '' "
               "ORDER BY a.subdomain, p.publication_date, p.id"):
        d["sub_pubs"][r["sd"]].append((r["id"], r["title"], r["publication_date"],
                                       r["journal"], r["publication_type"]))
    for r in q("SELECT DISTINCT a.subdomain AS sd, au.id, au.display_name AS name "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "JOIN publication_author pau ON pau.publication_id = pa.publication_id "
               "JOIN author_display au ON au.id = pau.author_id "
               "WHERE COALESCE(a.subdomain, '') <> '' "
               "ORDER BY a.subdomain, au.display_name"):
        d["sub_authors"][r["sd"]].append((r["id"], r["name"]))
    # The 'uses' side: the sequencers these papers ran, as opposed to the
    # workflows they contributed.
    for r in q("SELECT DISTINCT a.subdomain AS sd, tool.id, tool.name "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "JOIN publication_algorithm used ON used.publication_id = pa.publication_id "
               "     AND used.role = 'uses' "
               "JOIN algorithm tool ON tool.id = used.algorithm_id "
               "WHERE COALESCE(a.subdomain, '') <> '' "
               "ORDER BY a.subdomain, tool.name"):
        d["sub_tools"][r["sd"]].append((r["id"], r["name"]))
    for r in q("SELECT DISTINCT a.subdomain AS sd, c.name AS country "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "JOIN publication_author pau ON pau.publication_id = pa.publication_id "
               "JOIN author_affiliation aa ON aa.author_id = pau.author_id "
               "JOIN affiliation af ON af.id = aa.affiliation_id "
               "JOIN city ci ON ci.id = af.city_id "
               "JOIN country c ON c.id = ci.country_id "
               "WHERE COALESCE(a.subdomain, '') <> '' "
               "ORDER BY a.subdomain, c.name"):
        d["sub_countries"][r["sd"]].append(r["country"])

    # Architecture families, and everything that hangs off each one. The page
    # policy -- a family needs two or more methods -- lives in slugs.py's
    # ENTITY_QUERIES and is repeated here rather than imported, because these
    # queries aggregate over the members and the HAVING clause is what defines
    # the member set. The two are checked against each other in main(): a family
    # with no slug gets no page.
    # The one thing these pages cannot derive: a line of curated prose saying
    # what the family's methods have in common. Keyed by name and holding
    # nothing else, so it cannot decide which families exist (that is the
    # HAVING clause) or where their pages live (that is MIN(id)).
    d["fam_blurb"] = dict(q("SELECT name, blurb FROM family_note ORDER BY name"))
    d["families"] = []
    for r in q("SELECT MIN(id) AS k, algorithm_family AS name, COUNT(*) AS n "
               "FROM algorithm WHERE COALESCE(algorithm_family, '') <> '' "
               "GROUP BY algorithm_family HAVING COUNT(*) >= 2 "
               "ORDER BY algorithm_family"):
        d["families"].append({"key": r["k"], "name": r["name"], "n": r["n"]})
    fam_names = {f["name"] for f in d["families"]}
    # name -> the slug key, for the Family row on an algorithm page. Absent for
    # the single-method families, which deliberately have no page.
    d["family_key"] = {f["name"]: f["key"] for f in d["families"]}

    # Methods oldest first, which is how a family reads as a history: the method
    # that introduced the family leads, and the newest arrival is last.
    d["fam_methods"] = defaultdict(list)
    for r in q("SELECT a.algorithm_family AS fam, a.id, a.name, a.kind, "
               "       a.is_deep_learning AS dl, a.acquisition_mode AS mode, "
               "       a.subdomain, a.short_description AS descr, "
               "       MIN(p.publication_date) AS first_pub "
               "FROM algorithm a "
               "LEFT JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "     AND pa.role = 'describes' "
               "LEFT JOIN publication p ON p.id = pa.publication_id "
               "WHERE COALESCE(a.algorithm_family, '') <> '' "
               "GROUP BY a.id ORDER BY first_pub IS NULL, first_pub, a.name"):
        if r["fam"] in fam_names:
            d["fam_methods"][r["fam"]].append(dict(r))

    # Papers, authors and application areas all come through the DESCRIBING
    # links only. Taking them from every link would hand Transformer (AR) the
    # snake-venom papers that merely ran Casanovo, which is the same error the
    # role column was added to fix on the algorithm pages.
    d["fam_pubs"] = defaultdict(list)
    for r in q("SELECT DISTINCT a.algorithm_family AS fam, p.id, p.title, "
               "       p.publication_date AS date, p.journal, p.publication_type AS ptype "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "     AND pa.role = 'describes' "
               "JOIN publication p ON p.id = pa.publication_id "
               "WHERE COALESCE(a.algorithm_family, '') <> '' "
               "ORDER BY a.algorithm_family, p.publication_date, p.id"):
        if r["fam"] in fam_names:
            d["fam_pubs"][r["fam"]].append(
                (r["id"], r["title"], r["date"], r["journal"], r["ptype"]))

    d["fam_authors"] = defaultdict(list)
    for r in q("SELECT DISTINCT a.algorithm_family AS fam, au.id, "
               "       au.display_name AS name "
               "FROM algorithm a "
               "JOIN publication_algorithm pa ON pa.algorithm_id = a.id "
               "     AND pa.role = 'describes' "
               "JOIN publication_author pau ON pau.publication_id = pa.publication_id "
               "JOIN author_display au ON au.id = pau.author_id "
               "WHERE COALESCE(a.algorithm_family, '') <> '' "
               "ORDER BY a.algorithm_family, au.display_name"):
        if r["fam"] in fam_names:
            d["fam_authors"][r["fam"]].append((r["id"], r["name"]))

    d["metric_urls"] = {r["url"] for r in
                        q("SELECT url FROM repository_metrics ORDER BY url")}

    # Benchmark standing per algorithm, for the one line each method page gets.
    #
    # Unlike repository stars, these ARE baked in. Stars move every day, so
    # putting them on a page would rewrite 2485 pages nightly; benchmark results
    # move when an upstream repository commits new runs, which is weeks apart,
    # and the refresh workflow only commits when they actually changed. The cost
    # is that such a week triggers a full render instead of an index-only one.
    d["bench"] = {}
    rows = list(q(
        "SELECT t.algorithm_id AS aid, t.version, r.dataset, r.ap_peptide AS ap,"
        "       RANK() OVER (PARTITION BY r.dataset ORDER BY r.ap_peptide DESC) AS rnk "
        "FROM benchmark_result r "
        "JOIN benchmark_tool t ON t.tool = r.tool "
        "WHERE r.is_latest = 1 AND r.ap_peptide IS NOT NULL "
        "  AND t.algorithm_id IS NOT NULL "
        # Only the tools evaluated on every dataset are ranked against each
        # other, which is the same filter the charts use.
        "  AND t.n_datasets = (SELECT COUNT(*) FROM benchmark_dataset) "
        "ORDER BY t.algorithm_id, r.dataset"))
    n_ranked = len({r["aid"] for r in rows})
    for aid, group in groupby(rows, key=lambda r: r["aid"]):
        group = list(group)
        d["bench"][aid] = {
            "version": group[0]["version"],
            "n_datasets": len(group),
            "median_ap": median(r["ap"] for r in group),
            "median_rank": median(r["rnk"] for r in group),
            "of": n_ranked,
        }

    # denovo_benchmarks' evaluation sets, listed on the page of the benchmark's
    # own deposit (MSV000096182), which is where "over 84 datasets" links; and
    # the nine-species benchmark, where ProteoBench's selection is a version.
    d["bench_datasets"] = [dict(r) for r in q(
        "SELECT name, category, cat_group, n_tools FROM benchmark_dataset "
        "ORDER BY cat_group, category, name")]
    _b = q("SELECT v.dataset_id FROM dataset_address a JOIN dataset_version v "
           "ON v.id = a.dataset_version_id WHERE a.accession = 'MSV000096182' LIMIT 1")
    d["bench_ds_id"] = (list(_b) or [[None]])[0][0]
    _n = q("SELECT id FROM dataset WHERE name = 'Nine-species benchmark'")
    d["nine_ds_id"] = (list(_n) or [[None]])[0][0]

    d["proteobench"] = {}
    for r in q(
            "SELECT s.algorithm_id AS aid, s.version, s.decoding, s.submitted,"
            "       m.level, m.precision, m.auc, m.coverage "
            "FROM proteobench_submission s "
            "JOIN proteobench_metric m ON m.submission_id = s.id "
            "WHERE s.algorithm_id IS NOT NULL AND m.match_type = 'mass' "
            "ORDER BY s.algorithm_id, m.level"):
        entry = d["proteobench"].setdefault(r["aid"], {
            "version": r["version"], "decoding": r["decoding"],
            "submitted": r["submitted"]})
        entry[r["level"]] = (r["precision"], r["auc"], r["coverage"])

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
    # THE COMPARISON TABLES THIS METHOD'S OWN PAPERS PRINT, standardised: one
    # per printed table (or dataset part of one), from the verified rows of
    # paper_comparison. Keyed by the paper's OWN method, the result rows with
    # is_self = 1, so a table lands on the page of the method it was printed to
    # promote and nowhere else.
    #
    # ONE COPY OF A TABLE PRINTED TWICE. A preprint and its version of record
    # usually carry the same table, and showing both says the same thing twice.
    # Two tables are the same when what they REPORT is the same -- every method,
    # variant, metric, level, species and value -- which also catches
    # pairs publication_version does not link (a postprint and its conference
    # paper). The kept copy is the most authoritative publication; the other is
    # named under it. Tables that differ in anything are both shown, because
    # the difference is the point.
    d["comparisons"] = defaultdict(list)
    TYPE_RANK = {"peer-reviewed": 0, "ML conference": 1, "postprint": 2,
                 "preprint": 3, "thesis": 4}
    comps = {r["id"]: dict(r) for r in q(
        "SELECT c.id, c.review_id, c.publication_id, c.table_label, c.part, c.pdf_page,"
        "       c.caption, c.footnote, c.design_note, c.dataset_id, c.dataset_printed,"
        "       c.unit_printed,"
        "       ds.name AS dataset, dv.version AS dataset_version,"
        "       p.title, p.publication_date, p.publication_type "
        "FROM paper_comparison c JOIN publication p ON p.id = c.publication_id "
        "LEFT JOIN dataset ds ON ds.id = c.dataset_id "
        "LEFT JOIN dataset_version dv ON dv.id = c.dataset_version_id "
        # Comparisons only: a table of a paper's own results (one method, no
        # baselines) is not "reported comparisons", and feeds the
        # Performance-in-release-order chart instead.
        "WHERE c.review_status = 'verified' AND c.kind = 'comparison'")}
    for c in comps.values():
        c["results"] = []
        c["notes"] = []
    for r in q("SELECT r.*, a.name AS algorithm, cell.text_printed "
               "FROM paper_comparison_result r "
               "JOIN algorithm a ON a.id = r.algorithm_id "
               "JOIN paper_comparison_cell cell ON cell.comparison_id = r.comparison_id "
               " AND cell.row_index = r.row_index AND cell.col_index = r.col_index "
               "ORDER BY r.comparison_id, r.row_index, r.col_index, r.part_index"):
        if r["comparison_id"] in comps:
            comps[r["comparison_id"]]["results"].append(dict(r))
    for r in q("SELECT comparison_id, text FROM paper_comparison_note "
               "ORDER BY comparison_id, note_index"):
        if r["comparison_id"] in comps:
            comps[r["comparison_id"]]["notes"].append(r["text"])
    by_subject: dict[int, list[dict]] = defaultdict(list)
    for c in comps.values():
        # The BASIS is left out on purpose: it is our reading of each paper's
        # prose, not part of the printed table. CrossNovo's two preprints print
        # the same Table 2, and one's prose licenses 'retrained' where the
        # other's says nothing; that is not two tables.
        c["signature"] = frozenset(
            (x["algorithm_id"], x["variant_printed"] or "", x["metric"], x["level"],
             x["subset_canonical"] or "", round(x["value"], 4))
            for x in c["results"])
        for aid in {x["algorithm_id"] for x in c["results"] if x["is_self"]}:
            by_subject[aid].append(c)
    for aid, cs in by_subject.items():
        cs.sort(key=lambda c: (TYPE_RANK.get(c["publication_type"], 9),
                               -int(str(c["publication_date"] or "0")[:4] or 0),
                               c["publication_id"]))
        kept: list[dict] = []
        for c in cs:
            twin = next((k for k in kept if k["signature"] == c["signature"]
                         and k["publication_id"] != c["publication_id"]), None)
            if twin:
                twin.setdefault("also_in", []).append(c)
            else:
                kept.append({**c})
        kept.sort(key=lambda c: (str(c["publication_date"] or ""), c["pdf_page"],
                                 c["table_label"], c["part"] or ""))
        d["comparisons"][aid] = kept

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
    # Every path this run produced, so anything else under a generated
    # directory can be pruned afterwards. Without this the generator only ever
    # ADDS: renaming an algorithm or giving an author a disambiguator leaves the
    # old .qmd behind for ever. That happened twice -- six stale pages survived
    # the MS BLAST rename and four author disambiguations -- and it also breaks
    # render_scope.py, whose manifest is keyed by the page paths on disk: a
    # stale .qmd would keep its published .html alive for ever.
    produced: set[Path] = set()

    # Per-directory metadata, written by the generator so CI needs nothing
    # committed under pages/. search: false keeps ~6279 thin pages out of
    # search.json, which every visitor downloads before their first keystroke.
    # Little is lost: index.qmd's own "Browse all papers" / "Browse all authors"
    # tables already search the same data, with filters, and more usefully.
    # Measured: this does NOT speed up the render (67.8s vs 64.9s over 234
    # pages, i.e. noise); the win is purely index size, 508 KB back down to
    # 156 KB.
    # The `description` front matter is there for <meta name="description">,
    # which is what Google shows as the snippet. Quarto ALSO renders it into the
    # title block as a visible <div class="description">, so every page showed
    # the description clipped to 250 chars immediately above the same text in
    # full -- two slightly different summaries, the first one truncated. Keep
    # the meta tag, hide the visible copy. Declared per directory rather than
    # per page: all three levels of include-in-header merge (project, this, and
    # the per-page canonical), verified.
    METADATA = (
        "# Generated by build_pages.py. Do not edit.\n"
        "search: false\n"
        "repo-actions: false\n"
        "toc: false\n"
        "format:\n"
        "  html:\n"
        "    include-in-header:\n"
        "      - text: |\n"
        "          <style>#title-block-header .description { display: none; }</style>\n"
    )

    def emit(kind: str, slug: str, body: str, mtime: float) -> None:
        written[kind] = written.get(kind, 0) + 1
        if args.dry_run:
            return
        body = with_canonical(body, f"{SITE_URL}pages/{kind}/{slug}.html")
        # A retired URL redirects here: Quarto writes a redirect page at each
        # alias, so a renamed slug costs nobody a 404. See REDIRECTS in slugs.py.
        olds = sorted(o for o, n in REDIRECTS.get(kind, {}).items() if n == slug)
        if olds:
            end = body.index("\n---\n")
            body = (body[:end] + "\naliases:\n"
                    + "".join(f"  - {o}.html\n" for o in olds).rstrip("\n") + body[end:])
        path = args.out / kind / f"{slug}.qmd"
        produced.add(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rewrite when the CONTENT differs, not just when the file is absent.
        # These are generated files ("Do not edit"), and an existence-only check
        # meant a change to METADATA never reached a tree that had been built
        # before -- the description-hiding style silently did nothing.
        meta = path.parent / "_metadata.yml"
        if not meta.exists() or meta.read_text(encoding="utf-8") != METADATA:
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
            algs = [(a, n, desc, role)
                    for a, n, desc, _k, role in d["pub_algs"].get(pid, [])]
            counterpart = None
            if pid in d["versions"]:
                relation, other_id = d["versions"][pid]
                other = next((p for p in d["publications"] if p["id"] == other_id), None)
                if other:
                    counterpart = (other, relation)
            ji = d["journal_impact"].get(row["journal"] or "")
            ctx = {
                # Indexed, not unpacked with a star: the tuple grew a role at
                # the end, and `for *_x, k in` would have silently read that.
                "kinds": [t[3] for t in d["pub_algs"].get(pid, [])],
                "authors": authors,
                "algorithms": algs,
                "cites": d["cites"].get(pid, []),
                "cited_by": d["cited_by"].get(pid, []),
                "cited_by_count": d["impact"].get(pid),
                "venue_citedness": ji[0] if ji else None,
                "venue_ids": d["venue_key"],
                "counterpart": counterpart,
                "supervisors": d["supervisors_of"].get(pid, []),
                "datasets": d["pub_datasets"].get(pid, []),
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
            # Only the methods this author's papers DESCRIBE. Counting a
            # 'uses' link here credited every venomics author with PEAKS,
            # which the page then headlined as "Works on PEAKS".
            algs, seen = [], set()
            for pub_id, *_rest in d["author_pubs"].get(aid, []):
                for a, n, _desc, _k, role in d["pub_algs"].get(pub_id, []):
                    if role == "describes" and n not in seen:
                        seen.add(n)
                        algs.append((a, n))
            ctx = {
                "pubs": d["author_pubs"].get(aid, []),
                "supervisor": d["supervisor_for_student"].get(aid, []),
                "supervised": d["supervised_by"].get(aid, []),
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
            # Byline order, not alphabetical. 582 of 709 algorithms have exactly
            # one paper, so for most pages the byline is unambiguous and sorting
            # by name simply loses it: Denovo-GCN read "Haipeng Wang, Ruitao Wu,
            # Runtao Wang, Xiang Zhang" against a byline of "Ruitao Wu, Xiang
            # Zhang, Runtao Wang, Haipeng Wang".
            #
            # For a method with several papers, walk them OLDEST first so the
            # defining paper's byline leads, then append anyone who first
            # appears on a later paper. alg_pubs is newest-first for the Papers
            # section, hence the reversed().
            #
            # Authors come from the DESCRIBING papers only. Taking them from
            # every linked paper made PEAKS an entry with 107 authors, most of
            # whom had simply run it on a snake venom.
            # alg_pubs is newest-first. The describing papers are flipped to
            # oldest-first: the paper that introduced the method should lead the
            # section, and its byline should lead the author list. The using
            # papers stay newest-first, where the recent applications are.
            pubs = d["alg_pubs"].get(gid, [])
            described = [t for t in pubs if t[5] == "describes"][::-1]
            used = [t for t in pubs if t[5] == "uses"]
            authors, seen = [], set()
            for pub_id, *_r in described:
                for a, name in d["pub_authors"].get(pub_id, []):
                    if name not in seen:
                        seen.add(name)
                        authors.append((a, name))
            repos = d["repos"].get(gid, [])
            ctx = {
                "described": described,
                "used": used,
                "repos": repos,
                "checkpoints": d["checkpoints"].get(gid, []),
                "authors": authors,
                "has_metrics": any(u in d["metric_urls"] for u in repos),
                "has_prolific_author": any(a in d["prolific"] for a, _n in authors),
                "bench": d["bench"].get(gid),
                "proteobench": d["proteobench"].get(gid),
                "bench_ds_id": d["bench_ds_id"], "nine_ds_id": d["nine_ds_id"],
                "comparisons": d["comparisons"].get(gid, []),
                "subdomain": d["subdomain_by_name"].get(row["subdomain"]),
                "family_key": d["family_key"].get(row["algorithm_family"]),
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

    if "datasets" in kinds:
        pub_titles = {p["id"]: p["title"] for p in d["publications"]}
        for row in d["datasets"]:
            did = row["id"]
            ctx = {
                "versions": d["ds_versions"].get(did, []),
                "addrs": d["ds_addrs"],
                "pubs": d["ds_pubs"].get(did, []),
                "methods": d["ds_methods"].get(did, []),
                "checkpoints": d["ds_checkpoints"].get(did, []),
                "pub_titles": pub_titles,
                "bench_list": d["bench_datasets"] if did == d["bench_ds_id"] else [],
            }
            body, mtime = render_dataset(site, row, ctx)
            emit("datasets", site.slugs["datasets"][did], body, mtime)

    if "subdomains" in kinds:
        for row in d["subdomains"]:
            name = row["name"]
            pubs = d["sub_pubs"].get(name, [])
            dates = sorted(p[2] for p in pubs if p[2])
            ctx = {
                "methods": d["sub_methods"].get(name, []),
                "pubs": pubs,
                "authors": d["sub_authors"].get(name, []),
                "tools": d["sub_tools"].get(name, []),
                "countries": d["sub_countries"].get(name, []),
                "span": (dates[0], dates[-1]) if dates else None,
            }
            body, mtime = render_subdomain(site, row, ctx)
            emit("subdomains", site.slugs["subdomains"][row["id"]], body, mtime)

    if "families" in kinds:
        for fam in d["families"]:
            name = fam["name"]
            methods = d["fam_methods"][name]
            pubs = d["fam_pubs"].get(name, [])
            dates = sorted(p[2] for p in pubs if p[2])
            subs, seen = [], set()
            for m in methods:
                pair = d["subdomain_by_name"].get(m["subdomain"])
                if pair and pair[0] not in seen:
                    seen.add(pair[0])
                    subs.append(pair)
            ctx = {
                "methods": methods,
                "blurb": d["fam_blurb"].get(name),
                "pubs": pubs,
                "authors": d["fam_authors"].get(name, []),
                "subdomains": subs,
                "bench": d["bench"],
                "span": (dates[0], dates[-1]) if dates else None,
            }
            body, mtime = render_family(site, fam, ctx)
            emit("families", site.slugs["families"][fam["key"]], body, mtime)

    # Prune only the directories this run actually generated, so
    # `--only authors` cannot delete the publication pages.
    pruned = 0
    if not args.dry_run:
        for kind in kinds:
            d = args.out / kind
            if not d.is_dir():
                continue
            for stale in sorted(d.glob("*.qmd")):
                if stale not in produced:
                    stale.unlink()
                    pruned += 1
    if pruned:
        print(f"pruned {pruned} page(s) no longer in the catalog")

    total = sum(written.values())
    print("Done. " + ", ".join(f"{v} {k}" for k, v in sorted(written.items()))
          + f" = {total} pages total.")
    if args.dry_run:
        print("--dry-run: nothing was written.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
