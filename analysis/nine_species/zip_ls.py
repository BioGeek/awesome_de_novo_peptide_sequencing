import sys, zipfile, collections
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from remote import HTTPFile
for url in sys.argv[1:]:
    z = zipfile.ZipFile(HTTPFile(url))
    infos = z.infolist()
    print(f"== {url.split('/files/')[-1].split('?')[0]}: {len(infos)} members, {sum(i.file_size for i in infos)/1e9:.1f} GB uncompressed")
    by = collections.defaultdict(lambda: [0, 0])
    for i in infos:
        d = i.filename.rsplit("/", 1)[0] if "/" in i.filename else "."
        by[d][0] += 1; by[d][1] += i.file_size
    for d, (c, s) in sorted(by.items()): print(f"   {c:5d} files {s/1e9:8.2f} GB  {d}")
    print("   e.g.", [i.filename for i in infos[:4]])
