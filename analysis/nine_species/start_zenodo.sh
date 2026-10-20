#!/bin/sh
cd ~/data/ninespecies/raw
for z in balanced intermediate main; do
  setsid nohup ../.venv/bin/python ../scripts/pdl.py https://zenodo.org/records/13685813/files/nine-species-$z.zip nine-species-$z.zip 16 > ../log_zen_$z.txt 2>&1 < /dev/null &
done
