"""Reduce the parquet / IPC sources (InstaNovo split, InstaNovo per-species copy, dIon) to
the same per-spectrum schema as extract.py, and the Percolator PSM tables to their own."""
import sys, os, json, zipfile, io
import numpy as np, pyarrow as pa, pyarrow.parquet as pq, pyarrow.ipc as ipc, polars as pl
sys.path.insert(0, os.path.dirname(__file__))
from extract import Writer, norm_seq, canon_species
B = os.path.expanduser("~/data/ninespecies"); R = f"{B}/raw"; T = f"{B}/tables"

def summary(mz, it):
    mz = np.asarray(mz, dtype=np.float64); it = np.asarray(it, dtype=np.float64)
    if mz.size == 0: return 0, 0.0, [], None, None
    top = mz[np.argsort(-it, kind="stable")[:10]]
    return int(mz.size), float(it.sum()), sorted(round(float(x), 2) for x in top), float(mz.min()), float(mz.max())

def rows_from_batches(batches, out, source, species_of, split=None, fields=None):
    w = Writer(out); i = 0
    for b in batches:
        d = b.to_pydict()
        for k in range(b.num_rows):
            g = lambda c: d[c][k] if c in d else None
            n, tic, top, lo, hi = summary(g("mz_array"), g("intensity_array"))
            raw_label = g("modified_sequence") or g("peptide_id") or g("sequence")
            seq, bare = norm_seq(raw_label)
            pm = g("precursor_mz")
            extra = {c: g(c) for c in (fields or []) if c in d}
            w.add(dict(source=source, species=species_of(g), raw_file=None,
                       scan=int(g("scans")) if g("scans") and str(g("scans")).isdigit() else None,
                       title=g("title"), split=split, fold=None, row=i,
                       pepmass_str=repr(pm) if pm is not None else None, pepmass=pm,
                       charge=g("precursor_charge"), rt=g("retention_time"),
                       seq_raw=raw_label, seq_norm=seq, seq_bare=bare, n_peaks=n, tic=tic,
                       top10_mz=top, mz_min=lo, mz_max=hi,
                       extra=json.dumps(extra, default=str) if extra else None))
            i += 1
    w.close(); return w.n

def done(out): return os.path.exists(out)
what = sys.argv[1]
if what == "instanovo_split":
    for split, files in [("test", ["test-00000-of-00001.parquet"]), ("valid", ["validation-00000-of-00001.parquet"]),
                         ("train", ["train-00000-of-00002.parquet", "train-00001-of-00002.parquet"])]:
        out = f"{T}/instanovo_split__{split}.parquet"
        if done(out): continue
        batches = (b for f in files for b in pq.ParquetFile(f"{R}/{f}").iter_batches(batch_size=20000))
        print(split, rows_from_batches(batches, out, "instanovo_split", lambda g: None, split, ["sequence"]), flush=True)
elif what == "instanovo_ipc":
    for f in sorted(os.listdir(R)):
        if not f.endswith(".ipc"): continue
        sp = canon_species(f[:-4]); out = f"{T}/instanovo_ipc__{sp}.parquet"
        if done(out): continue
        r = ipc.open_file(pa.memory_map(f"{R}/{f}"))
        # One record batch can be the whole 8 GB file: read it in slices, never whole.
        batches = (r.get_batch(i).slice(o, 20000) for i in range(r.num_record_batches) for o in range(0, r.get_batch(i).num_rows, 20000))
        print(sp, rows_from_batches(batches, out, "instanovo_ipc", lambda g: canon_species(g("experiment_name")),
                                    None, ["experiment_name", "evidence_index", "scan_number", "sequence"]), flush=True)
elif what == "dion":
    for split in ["validation", "test"]:
        out = f"{T}/dion__{split}.parquet"
        if done(out): continue
        pf = pq.ParquetFile(f"{R}/dion_{split}_retrieval.parquet")
        print(split, rows_from_batches(pf.iter_batches(batch_size=20000), out, "dion", lambda g: canon_species(g("species")),
                                       "valid" if split == "validation" else "test",
                                       ["source_row_index", "selected_group_size", "peptide_id"]), flush=True)
elif what == "percolator":
    z = zipfile.ZipFile(f"{R}/nine-species-intermediate.zip")
    for m in z.namelist():
        if not m.endswith("percolator.target.psms.txt"): continue
        sp = m.split("/")[0]; out = f"{T}/percolator__{sp}.parquet"
        if done(out): continue
        with z.open(m) as f:
            df = pl.read_csv(f.read(), separator="\t", infer_schema_length=10000, quote_char=None)
        df = df.with_columns(pl.lit(sp).alias("species"))
        df.write_parquet(out, compression="zstd"); print(sp, df.shape, df.columns, flush=True)
