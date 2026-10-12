#!/usr/bin/env python3
"""Fail when a generated page links to an internal page that does not exist.

Every cross-page link in the catalog is a relative `.html` target (for example
`[InstaNovo](../algorithms/instanovo.html)`), written by `build_pages.py` and by
`index.qmd` from `denovo.db`. If a slug is computed one way for a page and another
way for a link to it, the link 404s -- invisible in a normal diff and only caught
later in Search Console. `slugs.py --check` guards URL *stability*; this guards URL
*existence*: it resolves every internal link to its source `.qmd` and reports any
that is missing.

stdlib-only, run from the repository root:

    python3 scripts/check_links.py

Exit status 0 when every internal link resolves, 1 (with a report) otherwise.
"""
import glob
import os
import re
import sys
from collections import defaultdict

# Markdown `](target.html)` and raw `href="target.html"`, optional #fragment.
LINK_RE = re.compile(r'\]\(([^)\s]+?\.html(?:#[^)\s]*)?)\)|href="([^"]+?\.html(?:#[^"]*)?)"')


def broken_links(root: str) -> dict[str, list[str]]:
    """Map each missing link target (repo-relative .qmd it should resolve to) to
    the source pages that reference it."""
    broken: dict[str, list[str]] = defaultdict(list)
    sources = ["index.qmd"] + sorted(glob.glob("pages/**/*.qmd", recursive=True))
    for src in sources:
        srcdir = os.path.dirname(src)
        for m in LINK_RE.finditer(open(os.path.join(root, src), encoding="utf-8").read()):
            target = (m.group(1) or m.group(2)).split("#")[0]
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            qmd = os.path.normpath(os.path.join(srcdir, target))[:-5] + ".qmd"
            if not os.path.exists(os.path.join(root, qmd)):
                broken[qmd].append(src)
    return broken


def main() -> int:
    root = os.getcwd()
    broken = broken_links(root)
    if not broken:
        print("check_links: all internal links resolve.")
        return 0
    refs = sum(len(v) for v in broken.values())
    print(f"check_links: {len(broken)} missing page(s), {refs} broken link(s):", file=sys.stderr)
    for target in sorted(broken):
        srcs = broken[target]
        shown = ", ".join(sorted(set(srcs))[:3])
        more = f" (+{len(set(srcs)) - 3} more)" if len(set(srcs)) > 3 else ""
        print(f"  MISSING {target}  <- {shown}{more}", file=sys.stderr)
    print("\nFix the slug (or add a REDIRECTS entry in scripts/slugs.py if a page "
          "was renamed), then rebuild with scripts/build_pages.py.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
