#!/bin/sh
cd ~/data/ninespecies
.venv/bin/python scripts/extract_tabular.py percolator > log_percolator.txt 2>&1
.venv/bin/python scripts/extract_tabular.py dion > log_dion.txt 2>&1
.venv/bin/python scripts/extract_tabular.py instanovo_ipc > log_ipc.txt 2>&1 && rm -f raw/*.ipc
