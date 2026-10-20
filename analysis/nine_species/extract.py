"""Reduce one nine-species source to a per-spectrum parquet table.

Every version of the benchmark ends up in the same schema, so the versions can be
joined: identity (species, raw_file, scan), split/fold, precursor, RT, the label
as printed and normalised, and a peak summary (count, TIC, top-10 peaks by
intensity) that says whether two versions hold the same spectrum.
"""
from __future__ import annotations
import gzip, io, re, sys, zipfile, ftplib, os, time, json, hashlib
import pyarrow as pa, pyarrow.parquet as pq

SPECIES = {  # every spelling seen in any version -> one canonical name
    "yeast": "Saccharomyces-cerevisiae", "saccharomyces_cerevisiae": "Saccharomyces-cerevisiae",
    "bacillus": "Bacillus-subtilis", "bacillus_subtilis": "Bacillus-subtilis",
    "clambacteria": "Candidatus-endoloripes", "candidatus_endoloripes": "Candidatus-endoloripes",
    "honeybee": "Apis-mellifera", "apis_mellifera": "Apis-mellifera",
    "human": "H.-sapiens", "h_sapiens": "H.-sapiens", "homo_sapiens": "H.-sapiens",
    "mmazei": "Methanosarcina-mazei", "methanosarcina_mazei": "Methanosarcina-mazei",
    "mouse": "Mus-musculus", "mus_musculus": "Mus-musculus",
    "ricebean": "Vigna-mungo", "vigna_mungo": "Vigna-mungo",
    "tomato": "Solanum-lycopersicum", "solanum_lycopersicum": "Solanum-lycopersicum",
}
CANON = set(SPECIES.values())
def canon_species(s):
    if s in CANON: return s
    return SPECIES.get(s.lower().replace("-", "_").replace(".", "").replace(" ", "_"), s)

# ---- label normalisation --------------------------------------------------
FLANK = re.compile(r"^[A-Z\-_]?\.(.*)\.[A-Z\-_]?$")
MODS = [  # (pattern, replacement) -> residue[+mass2dp]
    (re.compile(r"\(([+-]?\d*\.\d+)\)"), lambda m: f"[{float(m.group(1)):+.2f}]"),      # M(+15.99)
    (re.compile(r"\[\+?([+-]?\d*\.?\d+)\]"), lambda m: f"[{float(m.group(1)):+.2f}]"),  # M[15.9949], C[+57.0210]
    (re.compile(r"(?<=[A-Z\]])([+-]\d+\.\d+)"), lambda m: f"[{float(m.group(1)):+.2f}]"),  # M+15.995
]
NTERM_VALUES = {42.01, 43.01, -17.03, 25.98}   # acetyl, carbamyl, NH3 loss, carbamyl+NH3 loss

def norm_seq(s):
    """One form for one peptide, whatever the version wrote:
    'K.YDSTHGR.Y', 'C+57.021GHT', '.STM[15.9949]K.', 'M(+15.99)', '+42.011AAA', '+43.006-17.027AAA',
    Tide's N-terminal mod written on the first residue ('A[42.0106]AK') and a bare static C all map to
    e.g. '[+25.98]-AAAC[+57.02]K'. Returns (normalised, bare letters)."""
    if s is None: return None, None
    s = s.strip()
    m = FLANK.match(s)
    if m: s = m.group(1)
    nterm = 0.0
    # denovo_benchmarks writes an N-terminal mod as a lowercase 'n' residue: 'n[42.0106]AAK'.
    s = re.sub(r"^n\[([+-]?)(\d*\.\d+)\]", lambda m: f"[{m.group(1) or '+'}{m.group(2)}]-", s)
    lead = re.match(r"^((?:\[?[+-]\d*\.\d+\]?)+)-?(?=[A-Z])", s)        # leading N-terminal mod(s)
    if lead:
        nterm = sum(float(x) for x in re.findall(r"[+-]\d*\.\d+", lead.group(1))); s = s[lead.end():]
    for pat, rep in MODS: s = pat.sub(rep, s)
    # Tide (and InstaNovo's IPC copy) write an N-terminal mod on the first residue, after that
    # residue's own mod if it has one: 'Q[+42.01]...' and 'C[+57.02][+42.01]...'.
    tide = re.match(r"^([A-Z](?:\[[+-]\d+\.\d+\](?=\[))?)\[([+-]\d+\.\d+)\]", s)                 # Tide puts it on the first residue
    if tide and round(float(tide.group(2)), 2) in NTERM_VALUES:
        nterm += float(tide.group(2)); s = tide.group(1) + s[tide.end():]
    s = re.sub(r"C(?!\[)", "C[+57.02]", s)                               # static carbamidomethyl, made explicit
    seq = (f"[{nterm:+.2f}]-" if nterm else "") + s
    return seq, re.sub(r"[^A-Z]", "", s)

# ---- MGF streaming --------------------------------------------------------
def mgf_records(lines):
    """Yield dict per spectrum from an iterator of str lines."""
    rec, peaks = None, []
    for line in lines:
        if not line: continue
        c = line[0]
        if c.isdigit():
            if rec is not None:
                sp = line.split()
                if len(sp) >= 2: peaks.append((float(sp[0]), float(sp[1])))
            continue
        line = line.rstrip("\r\n")
        if line == "BEGIN IONS": rec, peaks = {}, []
        elif line == "END IONS":
            if rec is not None:
                rec["_peaks"] = peaks; yield rec
            rec = None
        elif rec is not None and "=" in line:
            k, v = line.split("=", 1); rec[k.upper()] = v

def peak_summary(peaks):
    if not peaks: return 0, 0.0, [], None, None
    top = sorted(peaks, key=lambda p: -p[1])[:10]
    return (len(peaks), float(sum(p[1] for p in peaks)), sorted(round(p[0], 2) for p in top),
            float(min(p[0] for p in peaks)), float(max(p[0] for p in peaks)))

SCHEMA = pa.schema([
    ("source", pa.string()), ("species", pa.string()), ("raw_file", pa.string()), ("scan", pa.int64()),
    ("title", pa.string()), ("split", pa.string()), ("fold", pa.string()), ("row", pa.int64()),
    ("pepmass_str", pa.string()), ("pepmass", pa.float64()), ("charge", pa.int8()), ("rt", pa.float64()),
    ("seq_raw", pa.string()), ("seq_norm", pa.string()), ("seq_bare", pa.string()),
    ("n_peaks", pa.int32()), ("tic", pa.float64()), ("top10_mz", pa.list_(pa.float32())),
    ("mz_min", pa.float32()), ("mz_max", pa.float32()), ("extra", pa.string()),
])
class Writer:
    def __init__(self, path):
        self.path, self.buf, self.w, self.n = path, [], None, 0
    def add(self, d):
        self.buf.append(d); self.n += 1
        if len(self.buf) >= 200_000: self.flush()
    def flush(self):
        if not self.buf: return
        t = pa.Table.from_pylist(self.buf, schema=SCHEMA)
        if self.w is None: self.w = pq.ParquetWriter(self.path + ".tmp", SCHEMA, compression="zstd")
        self.w.write_table(t); self.buf = []
    def close(self):
        self.flush()
        if self.w is None: pq.write_table(pa.Table.from_pylist([], schema=SCHEMA), self.path + ".tmp")
        else: self.w.close()
        os.replace(self.path + ".tmp", self.path)

ORIG_TITLE = re.compile(r"PeaksProjects\\([a-z]+)_PXD\d+_.*\\([^\\]+?)(?:\.raw)?_SCANS_(\d+)$", re.I)
def to_row(rec, source, species, raw_file, scan, split, fold, row, extra=None):
    seq_raw = rec.get("SEQ")
    seq, bare = norm_seq(seq_raw)
    n, tic, top, lo, hi = peak_summary(rec["_peaks"])
    pm = rec.get("PEPMASS", "").split()[0] if rec.get("PEPMASS") else None
    ch = rec.get("CHARGE", "").rstrip("+").split("+")[0] if rec.get("CHARGE") else None
    return dict(source=source, species=species, raw_file=raw_file, scan=scan, title=rec.get("TITLE"),
                split=split, fold=fold, row=row, pepmass_str=pm, pepmass=float(pm) if pm else None,
                charge=int(ch) if ch and ch.isdigit() else None,
                rt=float(rec["RTINSECONDS"]) if rec.get("RTINSECONDS") else None,
                seq_raw=seq_raw, seq_norm=seq, seq_bare=bare, n_peaks=n, tic=tic, top10_mz=top,
                mz_min=lo, mz_max=hi, extra=json.dumps(extra) if extra else None)

def scan_of(rec):
    s = rec.get("SCANS", "")
    # SCANS=, a 'scan=' in the TITLE (ThermoRawFileParser), or a 'raw.scan.scan.charge' TITLE
    # (denovo_benchmarks' MGFs in MSV000096182)
    m = (re.search(r"(\d+)$", s) or re.search(r"scan=(\d+)", rec.get("TITLE", ""))
         or re.search(r"\.(\d+)\.\d+\.\d+$", rec.get("TITLE", "")))
    return int(m.group(1)) if m else None

def run_mgf(lines, out, source, species=None, raw_file=None, split=None, fold=None, original=False):
    w = Writer(out)
    for i, rec in enumerate(mgf_records(lines)):
        sp, rf, sc = species, raw_file, scan_of(rec)
        if original:
            m = ORIG_TITLE.search(rec.get("TITLE", ""))
            if m: sp, rf, sc = canon_species(m.group(1)), m.group(2), int(m.group(3))
        w.add(to_row(rec, source, sp, rf, sc, split, fold, i))
    w.close(); return w.n

# ---- transports -----------------------------------------------------------
def ftp_download(path, dest):
    for attempt in range(8):
        try:
            ftp = ftplib.FTP_TLS("massive-ftp.ucsd.edu", timeout=300); ftp.login(); ftp.prot_p()
            done = os.path.getsize(dest) if os.path.exists(dest) else 0
            with open(dest, "ab") as f:
                ftp.retrbinary(f"RETR {path}", f.write, blocksize=1 << 20, rest=done or None)
            try: ftp.quit()
            except Exception: pass
            return
        except Exception as e:
            print(f"  retry {attempt} {path}: {e}", flush=True); time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"failed {path}")

def text_lines(binary):
    return io.TextIOWrapper(binary, encoding="utf-8", errors="replace", newline="")

if __name__ == "__main__":
    job = json.loads(sys.argv[1]); out = job["out"]
    if os.path.exists(out): print("skip", out); sys.exit(0)
    kind = job["kind"]; t0 = time.time()
    if kind == "ftp_mgf":
        tmp = out + ".download"
        ftp_download(job["path"], tmp)
        with open(tmp, "rb") as f:
            n = run_mgf(text_lines(f), out, job["source"], job.get("species"), job.get("raw_file"),
                        job.get("split"), job.get("fold"), job.get("original", False))
        os.remove(tmp)
    elif kind == "zip_mgf":
        with zipfile.ZipFile(job["zip"]) as z, z.open(job["member"]) as f:
            n = run_mgf(text_lines(f), out, job["source"], job.get("species"), None, job.get("split"))
    elif kind == "gz_mgf":
        with gzip.open(job["path"], "rb") as f:
            n = run_mgf(text_lines(f), out, job["source"], None, None, job.get("split"))
    print(f"{kind} {os.path.basename(out)}: {n} spectra in {time.time()-t0:.0f}s", flush=True)
