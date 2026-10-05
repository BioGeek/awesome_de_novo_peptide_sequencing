#!/usr/bin/env python3
"""The OpenAlex API key, for the builders that would otherwise share a budget.

OpenAlex's anonymous pool is a daily budget shared across every caller from an
IP, and a heavy day exhausts it. build_publication_impact.py hit that mid-run:
it aborted after ten consecutive empty lookups, which is the right behaviour --
it preserved the ids it already had rather than writing nothing over them --
but it meant the papers added that day had no openalex_id, and
build_affiliations.py silently skips anything without one.

A key moves those calls to a per-key budget. It is read from the environment
or from a `.env` beside this file, which is gitignored and must stay that way:
the key is a credential, not configuration.

Nothing here raises when the key is absent. Every builder worked without one
before and still does; the key only raises the ceiling, so an unkeyed clone is
a slower clone and not a broken one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV = Path(__file__).resolve().parent.parent / ".env"
_UNSET = object()
_cache: object = _UNSET


def api_key() -> str | None:
    """The key from $OPENALEX_API_KEY, else from .env, else None. Looked up once."""
    global _cache
    if _cache is not _UNSET:
        return _cache                      # type: ignore[return-value]
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not key and _ENV.exists():
        for line in _ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*OPENALEX_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                key = m.group(1).strip().strip("'\"")
                break
    _cache = key or None
    return _cache                          # type: ignore[return-value]


def with_key(url: str, params: dict | None) -> dict | None:
    """Add `api_key` to params when the request is going to OpenAlex.

    Keyed on the URL so a builder that also calls Crossref, Europe PMC or ORCID
    through the same helper never leaks the key to them.
    """
    if "api.openalex.org" not in (url or ""):
        return params
    key = api_key()
    if not key:
        return params
    out = dict(params or {})
    out.setdefault("api_key", key)
    return out
