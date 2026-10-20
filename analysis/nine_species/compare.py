"""Spectrum-level comparison of two versions on a shared key.

compare(a, b, on) joins two frames and classifies every spectrum:
  only_a / only_b / both, and for 'both' how the label, precursor, charge, RT and peaks changed.
"""
import polars as pl
from ns import il

LABEL_CLASSES = ["identical", "I/L only", "modifications differ", "different peptide"]

def label_class(a="seq_norm", b="seq_norm_b", abare="seq_bare", bbare="seq_bare_b"):
    return (pl.when(pl.col(a) == pl.col(b)).then(pl.lit("identical"))
            .when(il(pl.col(a)) == il(pl.col(b))).then(pl.lit("I/L only"))
            .when(il(pl.col(abare)) == il(pl.col(bbare))).then(pl.lit("modifications differ"))
            .otherwise(pl.lit("different peptide")))

def top10_jaccard():
    inter = pl.col("top10_mz").list.set_intersection(pl.col("top10_mz_b")).list.len()
    union = pl.col("top10_mz").list.set_union(pl.col("top10_mz_b")).list.len()
    return (inter / pl.when(union > 0).then(union).otherwise(None)).alias("top10_jaccard")

def compare(a: pl.DataFrame, b: pl.DataFrame, on: list[str], extra_b=()):
    cols = ["seq_norm", "seq_bare", "pepmass", "charge", "rt", "n_peaks", "top10_mz", *extra_b]
    bb = b.select(on + [c for c in cols if c in b.columns]).rename({c: f"{c}_b" for c in cols if c in b.columns})
    j = a.join(bb, on=on, how="full", coalesce=True)
    present_a = pl.col("seq_norm").is_not_null(); present_b = pl.col("seq_norm_b").is_not_null()
    j = j.with_columns(
        pl.when(present_a & present_b).then(pl.lit("both")).when(present_a).then(pl.lit("only_a")).otherwise(pl.lit("only_b")).alias("presence"))
    both = j.filter(pl.col("presence") == "both").with_columns(
        label_class().alias("label"),
        ((pl.col("pepmass_b") - pl.col("pepmass")) / pl.col("pepmass") * 1e6).alias("dmz_ppm"),
        (pl.col("charge_b") != pl.col("charge")).alias("charge_changed"),
        (pl.col("rt_b") - pl.col("rt")).alias("drt_s"),
        (pl.col("n_peaks_b") - pl.col("n_peaks")).alias("dpeaks"),
        top10_jaccard())
    return j, both

def summarise(j, both, by="species"):
    pres = j.group_by(by).agg(
        (pl.col("presence") == "both").sum().alias("both"),
        (pl.col("presence") == "only_a").sum().alias("only_a"),
        (pl.col("presence") == "only_b").sum().alias("only_b")).sort(by)
    lab = both.group_by(by).agg([(pl.col("label") == c).sum().alias(c) for c in LABEL_CLASSES]).sort(by)
    phys = both.group_by(by).agg(
        pl.col("charge_changed").sum().alias("charge_changed"),
        (pl.col("dmz_ppm").abs() > 5).sum().alias("mz_moved_gt5ppm"),
        pl.col("dmz_ppm").abs().median().alias("median_abs_dmz_ppm"),
        (pl.col("drt_s").abs() > 1).sum().alias("rt_moved_gt1s"),
        pl.col("drt_s").median().alias("median_drt_s"),
        (pl.col("dpeaks") != 0).sum().alias("peak_count_changed"),
        pl.col("dpeaks").median().alias("median_dpeaks"),
        pl.col("top10_jaccard").median().alias("median_top10_jaccard"),
        (pl.col("top10_jaccard") < 0.5).sum().alias("top10_jaccard_lt_0.5")).sort(by)
    return pres, lab, phys
