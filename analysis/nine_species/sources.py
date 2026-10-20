"""Every public input of the nine-species analysis, in one place.

download_sources.py fetches the HTTP ones, make_jobs.py and make_jobs_dnb.py list the FTP ones, and
page_numbers.py cites them by key, so the page's links and the pipeline's inputs cannot drift apart.
Nothing here is downloaded on import.
"""
MASSIVE_FTP = "massive-ftp.ucsd.edu"  # FTPS: the server refuses plain FTP


def massive_page(acc):
    return f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={acc}"


def massive_files(task):
    return f"https://massive.ucsd.edu/ProteoSAFe/dataset_files.jsp?task={task}"


MASSIVE = {
    # accession: (MassIVE task id for its file browser, FTP root)
    "MSV000081382": ("b7789710a31f488c9a74eb3e3b6f61eb", "/v01/MSV000081382"),
    "MSV000090982": ("e7c2e8518f10466bb4ce0e1070f43b90", "/v05/MSV000090982"),
    "MSV000096182": ("0411b1453d124decb7dc27351db4658d", "/v07/MSV000096182"),
}

ZENODO = "13685813"
ZENODO_DOI = "10.5281/zenodo.13685813"
HF_INSTANOVO = "InstaDeepAI/ms_ninespecies_benchmark"
HF_DION = "alfred-n/dIon-datasets"
PROTEOBENCH_MGF = "https://proteobench.cubimed.rub.de/raws/DeNovo-HCD/nine_species_balanced_De_Novo.mgf.gz"


def zenodo_file(name):
    return f"https://zenodo.org/records/{ZENODO}/files/{name}"


def hf_file(repo, path):
    return f"https://huggingface.co/datasets/{repo}/resolve/main/{path}"


def hf_tree(repo, path=""):
    return f"https://huggingface.co/datasets/{repo}/tree/main/{path}".rstrip("/")


SPECIES_FILES = ["apis_mellifera", "bacillus_subtilis", "candidatus_endoloripes", "h_sapiens", "methanosarcina_mazei",
                 "mus_musculus", "saccharomyces_cerevisiae", "solanum_lycopersicum", "vigna_mungo"]

# key -> (label shown on the page, URL a reader can open, how the pipeline fetches it)
SOURCES = {
    "v1": ("MSV000081382, HighResolution/data/high.<species>/peaks.db.mgf",
           massive_files(MASSIVE["MSV000081382"][0]), "FTPS, make_jobs.py"),
    "v1_folds": ("MSV000081382, HighResolution/data/cross.9high_80k.exclude_<species>/",
                 massive_files(MASSIVE["MSV000081382"][0]), "FTPS, make_jobs.py"),
    "massive_2022": ("MSV000090982, peak/", massive_files(MASSIVE["MSV000090982"][0]), "FTPS, make_jobs.py"),
    "massive_2023": ("MSV000090982, updates/2023-03-08_woutb_2c43c18c/",
                     massive_files(MASSIVE["MSV000090982"][0]), "FTPS, make_jobs.py"),
    "massive_2024": ("MSV000090982, updates/2024-05-14_woutb_71950b89/",
                     massive_files(MASSIVE["MSV000090982"][0]), "FTPS, make_jobs.py"),
    "v2_main": (f"{ZENODO_DOI}, nine-species-main.zip", zenodo_file("nine-species-main.zip"), "HTTP"),
    "v2_balanced": (f"{ZENODO_DOI}, nine-species-balanced.zip", zenodo_file("nine-species-balanced.zip"), "HTTP"),
    "v2_intermediate": (f"{ZENODO_DOI}, nine-species-intermediate.zip (Tide and Percolator output)",
                        zenodo_file("nine-species-intermediate.zip"), "HTTP"),
    "v2_data": (f"{ZENODO_DOI}, nine-species-data.zip (the converted MGFs)", zenodo_file("nine-species-data.zip"),
                "HTTP range requests, datazip.py"),
    "instanovo_split": (f"Hugging Face {HF_INSTANOVO}, data/{{train,validation,test}}-*.parquet",
                        hf_tree(HF_INSTANOVO, "data"), "HTTP"),
    "instanovo_ipc": (f"Hugging Face {HF_INSTANOVO}, data/ninespecies_updated/*.ipc",
                      hf_tree(HF_INSTANOVO, "data/ninespecies_updated"), "HTTP"),
    "proteobench": ("ProteoBench, nine_species_balanced_De_Novo.mgf.gz", PROTEOBENCH_MGF, "HTTP"),
    "dion": (f"Hugging Face {HF_DION}, representation_benchmarks/ninespecies/",
             hf_tree(HF_DION, "representation_benchmarks/ninespecies"), "HTTP"),
    "dnb": ("MSV000096182, other/9_species_*_labels.csv and peak/mgf/<raw file>_<chunk>.mgf",
            massive_files(MASSIVE["MSV000096182"][0]), "FTPS, make_jobs_dnb.py"),
    "wen_noble": ("Wen & Noble 2024, Scientific Data 11:1207", "https://doi.org/10.1038/s41597-024-04068-4", "paper"),
}

# The files download_sources.py fetches over HTTP, as (URL, local name under raw/).
HTTP_DOWNLOADS = (
    [(zenodo_file(f"nine-species-{z}.zip"), f"nine-species-{z}.zip") for z in ("main", "balanced", "intermediate")]
    + [(PROTEOBENCH_MGF, "nine_species_balanced_De_Novo.mgf.gz")]
    + [(hf_file(HF_INSTANOVO, f"data/{f}"), f) for f in
       ("test-00000-of-00001.parquet", "validation-00000-of-00001.parquet",
        "train-00000-of-00002.parquet", "train-00001-of-00002.parquet")]
    + [(hf_file(HF_INSTANOVO, f"data/ninespecies_updated/{s}.ipc"), f"{s}.ipc") for s in SPECIES_FILES]
    + [(hf_file(HF_DION, f"representation_benchmarks/ninespecies/{split}/retrieval.parquet"),
        f"dion_{split}_retrieval.parquet") for split in ("test", "validation")]
)
