"""Headers of every spectrum in Zenodo's nine-species-data.zip (the converted, unannotated MGFs),
streamed so the 59 GB archive never touches the disk.

Zenodo answers HTTP 429 to a burst of range requests, so the central directory is read ONCE and each
member is then fetched with a single streaming GET over its own byte range and inflated on the fly.
One parquet per member in tables/datazip__<species>__<raw>.parquet: scan, charge (null when the MGF gives
none), precursor m/z, RT and peak count. Finished members are skipped on a rerun.
"""
import os, re, struct, sys, time, zipfile, zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import polars as pl, requests
from remote import HTTPFile

URL = "https://zenodo.org/records/13685813/files/nine-species-data.zip?download=1"
T = os.path.expanduser("~/data/ninespecies/tables")
SCAN = re.compile(r"scan=(\d+)")

def stream_lines(url, start, length, method):
    """Yield the text lines of one zip member. The request starts at the member's LOCAL header, whose
    name and extra-field lengths are read off the stream, so no separate request is needed to find
    where the compressed data begins; 64 KB of slack covers the header."""
    for attempt in range(8):
        try:
            r = requests.get(url, headers={"Range": f"bytes={start}-{start + length + 65535}"}, stream=True, timeout=600)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 60)) + 5); continue
            if r.status_code != 206: raise IOError(f"HTTP {r.status_code}")
            d = zlib.decompressobj(-15) if method == zipfile.ZIP_DEFLATED else None
            tail, skip, left, head = b"", None, length, b""
            for chunk in r.iter_content(4 << 20):
                if skip is None:
                    head += chunk
                    if len(head) < 30: continue
                    n, e = struct.unpack("<HH", head[26:30]); skip = 30 + n + e
                    chunk = head[skip:]
                chunk = chunk[:left]; left -= len(chunk)
                if not chunk: break
                data = d.decompress(chunk) if d else chunk
                data = tail + data
                lines = data.split(b"\n"); tail = lines.pop()
                for ln in lines: yield ln
            if d: tail += d.flush()
            if tail: yield tail
            return
        except (requests.RequestException, IOError) as e:
            if attempt == 7: raise
            time.sleep(30 * (attempt + 1))

def parse(lines):
    rows, cur, peaks = [], None, 0
    for b in lines:
        if b[:1].isdigit():
            peaks += 1; continue
        line = b.decode("ascii", "replace").strip()
        if line == "BEGIN IONS":
            cur, peaks = {"scan": None, "charge": None, "pepmass": None, "rt": None, "title": None}, 0
        elif line == "END IONS":
            cur["n_peaks"] = peaks; rows.append(cur); cur = None
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            if k == "SCANS": cur["scan"] = int(v)
            elif k == "CHARGE": cur["charge"] = int(v.rstrip("+-") or 0) or None
            elif k == "PEPMASS": cur["pepmass"] = float(v.split()[0])
            elif k == "RTINSECONDS": cur["rt"] = float(v)
            elif k == "TITLE":
                cur["title"] = v
                if cur["scan"] is None:
                    m = SCAN.search(v); cur["scan"] = int(m.group(1)) if m else None
    return rows

def job(url, info, data_start):
    parts = info.filename.split("/")
    species, raw = parts[-2], parts[-1][:-4]
    out = f"{T}/datazip__{species}__{raw}.parquet"
    if os.path.exists(out): return out, "skip"
    t = time.time()
    try:
        rows = parse(stream_lines(url, data_start, info.compress_size, info.compress_type))
    except Exception as e:
        return out, f"FAILED {e}"
    df = pl.DataFrame(rows, schema={"scan": pl.Int64, "charge": pl.Int64, "pepmass": pl.Float64, "rt": pl.Float64,
                                    "title": pl.String, "n_peaks": pl.Int64})
    df = df.with_columns(pl.lit(species).alias("species"), pl.lit(raw).alias("raw_file"))
    df.write_parquet(out + ".part"); os.replace(out + ".part", out)
    return out, f"{df.height} spectra, {df['charge'].is_null().sum()} without charge, {time.time() - t:.0f}s"

if __name__ == "__main__":
    workers = int(os.environ.get("WORKERS", "12"))
    h = HTTPFile(URL); url = h.url
    z = zipfile.ZipFile(h)
    members = [i for i in z.infolist() if i.filename.endswith(".mgf")]
    members = [i for i in members if not os.path.exists(f"{T}/datazip__{i.filename.split('/')[-2]}__{i.filename.split('/')[-1][:-4]}.parquet")]
    starts = {i.filename: i.header_offset for i in members}
    members.sort(key=lambda i: -i.compress_size)
    print(f"{len(members)} MGFs to read, {sum(i.compress_size for i in members) / 1e9:.1f} GB compressed, {workers} streams", flush=True)
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for fut in as_completed([ex.submit(job, url, i, starts[i.filename]) for i in members]):
            out, msg = fut.result(); done += 1
            print(f"[{done}/{len(members)}] {os.path.basename(out)}: {msg}", flush=True)
