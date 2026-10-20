"""Segmented parallel HTTP download with per-segment resume. usage: pdl.py URL OUT [NSEG]"""
import os, sys, requests, time, threading
url, out = sys.argv[1], sys.argv[2]; nseg = int(sys.argv[3]) if len(sys.argv) > 3 else 16
h = requests.head(url, allow_redirects=True, timeout=60); size = int(h.headers["Content-Length"]); url = h.url
if os.path.exists(out) and os.path.getsize(out) == size: print("complete", out); sys.exit(0)
step = (size + nseg - 1) // nseg
parts = [(i, i * step, min(size, (i + 1) * step) - 1) for i in range(nseg)]
def fetch(i, a, b):
    p = f"{out}.part{i:02d}"
    while True:
        have = os.path.getsize(p) if os.path.exists(p) else 0
        if a + have > b: return
        try:
            with requests.get(url, headers={"Range": f"bytes={a + have}-{b}"}, stream=True, timeout=120) as r:
                if r.status_code != 206: raise IOError(r.status_code)
                with open(p, "ab") as f:
                    for chunk in r.iter_content(1 << 20): f.write(chunk)
        except Exception as e:
            print(f"seg {i}: {e}; retrying", flush=True); time.sleep(10)
ts = [threading.Thread(target=fetch, args=p) for p in parts]
[t.start() for t in ts]; [t.join() for t in ts]
with open(out + ".tmp", "wb") as o:
    for i, _, _ in parts:
        with open(f"{out}.part{i:02d}", "rb") as f:
            while (blk := f.read(1 << 24)): o.write(blk)
assert os.path.getsize(out + ".tmp") == size, "size mismatch"
os.replace(out + ".tmp", out)
for i, _, _ in parts: os.remove(f"{out}.part{i:02d}")
print("done", out, size, flush=True)
