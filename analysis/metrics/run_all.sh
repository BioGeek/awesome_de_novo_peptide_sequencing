#!/usr/bin/env bash
# Rebuild the metrics page's data and numbers from the two curated inputs beside this script:
# papers.jsonl (what each paper says it measured, with quotes) and implementations.json (what
# each tool's code does, with the commit it was read at). Stdlib Python only, no network.
set -euo pipefail
cd "$(dirname "$0")"
python3 build_site_data.py
python3 page_numbers.py
