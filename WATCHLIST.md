# Watch list

Tools that belong in the catalog but cannot be added yet, and things deliberately
left out. Keep it short: this is a note, not a process.

Every one of the 268 `algorithm` rows has at least one linked publication, because
`publication_algorithm` is how an algorithm gets its authors, its date on the
swim-lanes, its venue and its place in the citation graph. A tool with no
manuscript would have no date to plot and an empty Authors section on its
generated page. The only repository-only entry in the catalog is
`jingbo02 Awesome-Denovo-Peptide-Sequencing`, a `kind='meta'` link collection, and
the weakest method-level precedent is DiffNovo-DIA, backed by a thesis. So a method
waits here until it has something citable.

## Waiting for a manuscript

### SemiNovo

| | |
|---|---|
| Repo | <https://github.com/grandOrgan/Seminovo> (MIT) |
| Dataset | `DarkSpec`, 4.5M unlabeled PRIDE spectra, <https://huggingface.co/datasets/PanLiu/DarkSpec> |
| Author | Pan Liu, PhD candidate, HKUST (Guangzhou) |
| First released | 2026-07-27, a single "Initial SemiNovo release" commit |
| Likely title | "SemiNovo: Learning Beyond Search-Identified Spectra for De Novo Peptide Sequencing" (the repo description; the README tagline is the looser "Learning de novo peptide sequencing models from unlabeled tandem mass spectra") |
| Last checked | 2026-08-31 |

Semi-supervised sequencing: a FlashAttention spectrum encoder with multi-scale
Fourier peak features, a causal Transformer decoder, and an exponential-moving-average
teacher generating cumulative-confidence pseudo-labels so spectra that database
search discards can still train the model. Follows the NovoBench data and evaluation
protocol, Casanovo-style beam search.

Searched 2026-08-31 with nothing found: Crossref (both title variants), arXiv,
bioRxiv, OpenAlex, web search, and the repo's own README/NOTICE. OpenAlex returns
346 hits for "seminovo" and every one is Portuguese for "pre-owned car".
OpenReview was inconclusive: keyword-matching blind submissions exist but do not
expose titles, which is consistent with a paper under review. The repo and the
dataset were created within an hour of each other and the repo has one commit, which
looks like code released to accompany a submission.

Do not be misled by the acknowledged `PanLiuCSU/CSL` repo. That is
"Semi supervised semantic segmentation in ICCV 2025", a computer-vision paper cited
for its confidence-based pseudo-label selection, not the SemiNovo manuscript.

When a manuscript appears, the classification is already worked out:
`kind='algorithm'`, `algorithm_family='Transformer (AR)'`, `is_deep_learning=1`,
`acquisition_mode='DDA'` (NovoBench benchmarks are DDA), repo as above.

### Hellbender antimicrobial peptides

| | |
|---|---|
| Artifact | ASBMB Annual Meeting abstract, `10.1016/j.jbc.2026.112633`, J Biol Chem 302:112633 (May 2026) |
| Title | "Abstract 4402 De Novo Peptidomics and Bioprospecting Reveal Antimicrobial Peptide Candidates in *Cryptobranchus alleganiensis* (Hellbender) Skin Secretions" |
| Authors | Syeda Raika Shahid, Edward Bentil, Barney Bishop (George Mason University) |
| Method | PEAKS de novo sequencing of skin-secretion peptides, SPE enrichment, Orbitrap Fusion, then AMP prediction over the de novo sequences |
| Last checked | 2026-09-21 |

In scope on the merits and the classification is already clear: de novo
peptidomics is the subject rather than an aside, PEAKS is named, and AMP
candidates from an under-sampled amphibian are a real downstream application.
It waits here purely because of what the artifact IS.

The jbc.org `/fulltext` URL makes it look like a research article; it is not.
The title itself begins "Abstract 4402", OpenAlex types it `conference-abstract`,
and Crossref carries no abstract text, all consistent with the meeting
supplement. The catalog holds **no** meeting abstracts, and all nine
`ML conference` rows are full peer-reviewed proceedings papers (ICML, ICLR,
NeurIPS, IJCAI), not abstracts. A conference abstract is also thinner than the
weakest precedent named above, DiffNovo-DIA, which at least has a thesis behind
it, and no `publication_type` value fits without inventing an eighth.

Do not confuse it with "Novel antimicrobial peptides and peptide-microbiome
crosstalk in Appalachian salamander skin" (`10.1038/s41522-025-00837-0`, npj
Biofilms and Microbiomes, 2025). Different group (Muletz-Wolz, Smithsonian),
different species, no shared authors, and no de novo sequencing at all: it is
transcriptome-guided database search. Not a substitute and not a candidate.

When a full paper appears: `kind='downstream-application'`,
`subdomain='bioactive-peptides'` (or a new amphibian/AMP subdomain if several
such papers arrive together), `is_deep_learning=0`, `acquisition_mode='DDA'`,
linked to the existing PEAKS row.

## Considered, not added

- **CorrDIA** (`10.3390/app13105969`). DIA deconvolution feeding a conventional
  database search, with no *de novo* component, so it is out of scope even as an
  adjacent tool. Revisit only if it acquires one.
- **"Bioactive Peptides from Common Beans: A Review"** (MDPI *Nutraceuticals*
  6(3):62, 2026-09-17). A review of bean peptide bioactivity whose only *de novo*
  content is one generic sentence, naming no tool: "In instances where a reliable
  match is not available, de novo sequencing facilitates the inference of amino
  acid composition and order through the analysis of fragmentation patterns."

  This one also fixes the bar for `kind='review'`, which until now was only
  implicit in the rows themselves. **All 23 existing review entries have de novo
  sequencing as their subject, or as the method underpinning the body of work
  being reviewed**: that holds even for the domain-flavoured ones, which is why
  the snake venom proteomes review ("assembled largely via de novo sequencing")
  and Flying under the radar ("argues de novo identification is
  under-appreciated") qualify. There is no entry of the form "review of topic X
  that mentions de novo in passing", and admitting one would admit the unbounded
  set of food, venom and clinical peptide reviews that each carry a sentence like
  the above. A `review` row should promise that reading it teaches you something
  about de novo sequencing.

  Reconsider only if a review of this shape turns out to compare de novo tools,
  name software, or present de novo-derived sequences as evidence. Contrast the
  cricket hydrolysate paper (`10.1016/j.fufo.2026.101187`), same food-peptide
  space and same subdomain, which is IN because it ran PEAKS for 25,582
  assignments and the peptides it identified are the result.

- **"The genetic origin of evolidine, the first cyclopeptide discovered in plants,
  and related orbitides"** (`10.1074/jbc.RA120.014781`, J Biol Chem 2020; preprint
  `10.1101/2020.06.10.145326`). The worked example of a **false friend**: it uses
  *de novo* **transcriptomics**, which is not *de novo* peptide sequencing.

  Both phrases use "de novo" to mean "without a reference", and there the
  resemblance ends. *De novo* peptidomics is de novo peptide sequencing, reading
  sequence off MS/MS fragment ladders, applied to a peptidome: same technique as
  this catalog's subject, different analyte, so it counts. *De novo*
  transcriptomics is reference-free assembly of RNA-seq reads into transcripts:
  different molecule, different instrument, different algorithms, no mass
  spectrometry at all. The adjective modifies the assembly, not the sequencing.

  The paper settles it on its own terms. Its text has four uses of "de novo
  transcriptom*" and **zero** of "de novo sequenc*"; the single "de novo peptide
  sequencing" in the document is in reference 9, a citation. And the workflow runs
  the opposite way from de novo: "Having the sequences for transcripts encoding
  putative novel cyclic peptides facilitated their identification and sequencing
  from LC-MS/MS data. We found peaks corresponding to six additional peptides of
  ~13 predicted by transcriptomic data." The transcriptome supplied the candidate
  sequences and MS confirmed them, which is targeted matching against a custom
  database.

  Two traps worth knowing if you re-check it. Grepping for "PEAKS" gives three
  hits and none is the software: they are chromatographic and NMR *peaks*. And
  the PMCID is PMC7573267, one digit from PMC7573262, which is an unrelated
  chaperone paper.

  Worth revisiting only if the group publishes cyclopeptide work that actually
  sequences de novo. Reference 9 shows they know how: it is Behsaz et al.,
  "De novo peptide sequencing reveals many cyclopeptides in the human gut",
  the **CycloNovo** paper, already algorithm 53 and publication 56 here, with
  Joshua S. Mylne as its author 9. Following that reference is what exposed the
  seven truncated bylines fixed in 17b9a5e.

- **The Research Square posting of InstaNovo** (`10.21203/rs.3.rs-3376248/v1`,
  posted 2024-05-02). A second preprint of a paper already in the catalog, and a
  deliberate decision not to add it rather than an oversight.

  It is a genuinely distinct record, which is what makes it tempting: its title
  says "Diffusion-powered" where the bioRxiv row (publication 1) says "Accurate,
  database-free", and its first author is Timothy Jenkins rather than Kevin Eloff.
  By the BiATNovo precedent that is normally enough to earn its own row.

  The reason to leave it out is `publication_version`. Its UNIQUE index on
  `published_id` lets only one preprint claim the Nature Machine Intelligence
  paper, so adding this row forces a choice between a 19-month gap measured from
  bioRxiv and an 11-month gap measured from Research Square. Crossref would argue
  for the latter, since the Nature article's `has-preprint` names only the
  Research Square DOI, but the lifecycle chart is answering "how long until this
  work was formally published", and the honest clock starts when the work first
  became public in August 2023.

  Worth knowing for its own sake: that same Crossref asymmetry is why bioRxiv
  reports `published: "NA"` for `10.1101/2023.08.30.555055` to this day. The
  bioRxiv record carries no relation at all, so bioRxiv's matcher has nothing to
  work from. It is not a bioRxiv bug.

## Published elsewhere, already handled

`build_versions.py` reports any tracked preprint whose bioRxiv `published` field
names a journal DOI the catalog does not have. That list was empty as of
2026-08-31; the three it found (Pairwise Attention, Modanovo, Improvements to
Casanovo) are now in. Re-run it after adding papers rather than tracking those by
hand here.
