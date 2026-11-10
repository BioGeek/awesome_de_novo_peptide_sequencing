# Checkpoints

Split out of CLAUDE.md; read it when working on this area. Counts in it are kept current by `scripts/check_counts.py`, like CLAUDE.md's.

## Checkpoints

A benchmark number is only reproducible if the weights behind it can still be
downloaded, and this field keeps them in places with very different durability.
The `checkpoint` table records where they are and whether that is still true:
`(algorithm_id, label, tool_version, trained_on, host, url, accession, licence,
size_bytes, archival, status, http_code, last_checked, mirror_url, notes)`.
The 17 methods with a recorded checkpoint carry a **## Checkpoints** table on
their page, listing version, training data, host, licence, size and when the
link was last checked.

**One method name covers several models**, which is why `tool_version` and
`trained_on` are columns rather than prose. ProteoBench submits Casanovo
4.0.0, 4.2.0, 5.0.0 and 5.2.0-Orbitrap as separate datapoints
([discussion 356](https://github.com/orgs/Proteobench/discussions/356)), because
4.2.0 was trained on ~2M PSMs from MassIVE-KB v1 + v2.0.15 while 5.2.0 ships a
different default selector. Comparing a number against "Casanovo" without a
version is the same error as comparing one against "nine-species".

**`archival` is the durability class, not a label.** A DOI'd Zenodo or figshare
record is dated and preserved by its host; a Google Drive link, a personal
academic URL or a GitHub release tag is mutable, undated and can vanish leaving
nothing to cite. That distinction is the only reason to mirror anything.

### What the download actually found

The point of mirroring was link rot, and the survey found it in progress.

**Of nine Google Drive links, two were not files at all.** DeepNovo-DIA's is a
FOLDER holding `oc`, `plasma`, `uti`, a Windows installer, a code zip and
`train.urine_pain.ioncnn.lstm`, which is where its weights are: a TensorFlow
triple, `translate.ckpt-31800.data-00000-of-00001` plus its `.index` and a
`checkpoint` meta file, 184 MB. A file fetch against a folder id fails with a
permissions error, which reads like rot and is not.

**DeepNovo's is gated**, as above, and it is the one checkpoint here that cannot
be retrieved at all without a Google account until the catalog owner fetched it
from a signed-in session. Both DeepNovo entries turned out to be the awkward
ones: the least durably published weights are the ones hardest to rescue.

**The scan that preceded this was a lower bound and badly wrong.** Looking for
host names near checkpoint language in local PDFs found ONE Drive link. There
are nine, because the links live in repository READMEs rather than in papers:
`BEAM-Labs/denovo` publishes five in one table, and the ProteoBench discussion a
sixth. A paper's data-availability sentence is not where this field puts its
weights.

**Uploads fail partway and succeed on retry.** Three pushes to the mirror failed
after uploading 1 of 3, 3 of 3 and 3 of 4 objects, with `X-Cache: Error from
cloudfront` and 39 GB transferred for 4.4 GB of files, so git-lfs was re-sending
objects repeatedly. It is a flaky path through the TLS gateway, NOT a size
limit: the first diagnosis was that 2 GB objects were too large, and a plain
retry put both of them up. Push in a loop with `lfs.concurrenttransfers 1`.

**But not every failed push is that, and the two look identical in a log tail.**
Eight consecutive retries then failed on a `pre-receive hook declined`, which no
amount of retrying can fix: the Hub rejects anything over **10 MiB** that
bypasses LFS, and **a TensorFlow shard is named
`translate.ckpt-31800.data-00000-of-00001`, which does NOT match `*.ckpt`.**
The `.ckpt` sits in the MIDDLE of the name, so the pattern missed it, the 184 MB
file went in as a plain blob and the whole push was refused. `*.data-*` and
`*.index` are tracked now. Both failures end in `error: failed to push some
refs`; only the `remote:` lines above it say which one you have.

### The mirror, and the three places checkpoints appear

Backups of the checkpoints whose only home is a link with no DOI live in
<https://huggingface.co/BioGeek/denovo-checkpoints>, one folder per algorithm.

**Three surfaces, and each earns its place for a different reason**, which is
worth stating because a fourth was built and then deleted:

| surface | covers | regenerates |
|---|---|---|
| the Hugging Face README | the mirrored files | by hand, and it must exist: attribution has to travel with the bytes |
| **"The weights behind the numbers"** on index.qmd | every checkpoint, in one table | every render, from `denovo.db` |
| each algorithm page's `## Checkpoints` | that method's own | every `build_pages.py` |

A standalone `/checkpoints` page on the personal site was written, rendered and
then **deleted unpushed**, along with the `build_checkpoint_index.py` that fed
it. It was designed when the FILES were going to live at that URL; once they
moved to Hugging Face it added nothing the cross-method table does not, while
being the one copy that lived in a second repository and regenerated only when
somebody remembered a command. That is the duplicated-list failure this file
warns about for counts, and it applies to prose and tables too. **The place to
put a derived view is the project that owns the data.**

**The files cannot be served from jeroen.vangoey.be, which is where they were
first meant to go.** That domain is GitHub Pages: it **rejects any file over
100 MB on push**, and every checkpoint here is 270 MB to 2 GB. Git LFS does not
rescue it, because Pages serves the LFS pointer text rather than the object, and
a Pages site has a 1 GB published limit against 6.4 GB of weights. So the bytes
are on Hugging Face and only the index page is on Pages.

**The original link stays primary on every page.** A mirror is a fallback;
leading with it would obscure that the authors published the weights themselves,
and the original is what a paper should cite. The algorithm pages add a *copy*
column only when a mirror exists.

**Only weights are mirrored, and the data that was skipped is why that rule
exists.** Following every link in the sources would have cost **88 GB**:
`mskb_casanovo_splits.zip` is 63 GB, DIANovo's Dropbox folder is a 24 GB archive
of sample data with the weights inside it, and DeepNovo's Drive folder holds
`data`, `data.training` and `train.example`. DIANovo's weights were taken from
its **git-LFS** instead, 4.4 GB for the three `.pt` files, which is both smaller
and a better source than the Dropbox copy.

**Two operational notes, both learned the hard way.**

- **Stage multi-gigabyte work on the real disk.** Staging 4.4 GB of checkpoints
  plus an LFS clone in the session scratchpad filled a 16 GB tmpfs and killed
  the push with `ENOSPC`.
- **The Hugging Face Python client cannot reach the Hub from this machine**, for
  the same reason `requests` cannot reach publishers: the gateway's re-signed
  certificates carry no Authority Key Identifier, so `httpx` raises
  `CERTIFICATE_VERIFY_FAILED`. Create the repo with `curl` against
  `/api/repos/create` and push the files with `git` + `git-lfs`, both of which
  use a transport that accepts the certificate.

### build_checkpoints.py

This table's **only** writer, which is what would let it run under
`.github/actions/commit-refreshed-db`. It does not decide which checkpoints
exist: resolving a paper's data-availability sentence to a method is a judgement
call, so rows are added by hand and the script fills in `status`, `http_code`
and `last_checked`.

**A HEAD request is not enough on its own, and Google Drive is why.** Drive
answers **200 with an HTML interstitial** whether or not the file is still
shared, so a 200 there proves nothing. A 200 whose content type is HTML where a
binary was expected is recorded as `unverifiable` rather than `live`: the honest
answer is that the link resolves and what it resolves to cannot be confirmed
from a header.

**A DOWNLOAD OUTRANKS A PROBE.** `verified` means the bytes were fetched and
hashed, and `verified_at` says when; it is set from `sha256`, because a
completed download is stronger evidence than any HEAD request. It replaced
`unverifiable` and `moved` on 10 of the 11 rows whose contents the catalog
holds, which were the checker admitting it could not tell from a response
rather than a finding about the file. It does NOT override `dead` or `gated`:
those describe the ORIGINAL link and a reader needs them even when a copy
exists, which is exactly DeepNovo's case.

The pages print the date that matches the verdict: `verified_at` for a verified
row, `last_checked` for everything else. One date for both would conflate "we
have this file" with "this link answered".

Measured over the 32 recorded checkpoints: 21 live, 10 verified, 1 gated.

**A gated checkpoint can still be backed up, and DeepNovo now is.** Its weights
were retrieved from a signed-in session and mirrored, so the backup is the only
anonymous route to them. The status stays `gated`, because that describes the
ORIGINAL link, which is still unreachable without an account; what changed is
that a copy exists. Status and `mirror_url` answer different questions and the
pair is the useful reading.

The Drive folder held more than weights, which is the normal case: a yeast
FASTA, the `yeast.low.coon_2013` test spectra, training logs and decode output.
Only `translate.ckpt-48600`, its `.index` and meta went to the mirror, plus
`knapsack.npy.zip` -- not weights, but DeepNovo cannot run without it, so a
checkpoint alone would be unusable.

**`gated` is a status of its own, and DeepNovo earned it.** The pretrained model
for DeepNovo, the original deep-learning de novo method, now answers *"We can't
access this content right now. Try signing in to your Google Account"*. The
Kaiko paper cites that link as publicly available; it is no longer anonymously
downloadable, so the weights behind DeepNovo's published numbers cannot be
fetched without somebody's Google session. That is not `dead` and not merely
`unverifiable`, and a reader comparing against DeepNovo should see it.

**Detecting it depends on the URL FORM**, which cost a wrong verdict first time.
Drive's legacy `open?id=<id>` form returns a 944 KB application shell with no
gate text even when the file is gated; the canonical `/file/d/<id>/view` page
says so plainly. So `body_says_gated` extracts the id and tries both.

**Licence, not size, is what governs mirroring.** Of the non-archival
checkpoints, the five Casanovo and InstaNovo-FM GitHub releases are Apache-2.0
and redistributable; Winnow's HeLa QC model is **CC-BY-NC-SA-4.0**, so copying
it is permitted but conditional (non-commercial, share-alike, attributed); the
two DeepNovo repositories carry **custom non-commercial licences**; and the one
item with genuinely nothing stated is the `noble.gs.washington.edu/~melih` zip,
which is training data and was not mirrored.

**`NOASSERTION` means GitHub cannot CLASSIFY the licence, not that there is
none**, and reading it the second way was a mistake worth recording because it
produced a confident and wrong claim twice: that the two most at-risk
checkpoints "may not be copied", and that "silence is not permission" when
there was no silence. Both repositories have a LICENSE file:

> DeepNovo is publicly available for non-commercial uses. Copyright (C) 2017.
> Authors. All rights reserved.

> Copyright (C) 2018. Authors. All rights reserved. DeepNovo-DIA is provided
> free of charge for academic and non-commercial use.

Academic and non-commercial USE is granted; redistribution is not expressly
granted, which is a narrower and more accurate statement than the one it
replaced. **Read the LICENSE file, never the API's classification of it.**

Mirroring is therefore a deliberate manual step and **never part of a scheduled
refresh**: it copies someone else's bytes, publishes them, and commits this
project to keeping them alive, which a cron job should not decide. `--mirror`
reports the candidates and what blocks each one.

## Which dataset a checkpoint was trained on

`checkpoint_dataset` links the two, and it is **curated rather than derived**,
with an `evidence` column saying why each row exists. `checkpoint.trained_on` is
prose and cannot be joined on: its values include `~2M PSMs from MassIVE-KB v1 +
v2.0.15`, `the default --model orbitrap selector from v5.2.0 onward` and
`fine-tuned from model_massive.ckpt on the 2020-Cell-LUAD dataset`. A text match
would link a selector flag to nothing and miss the checkpoints whose only
evidence is a FILENAME.

8 links over 2 datasets, and the evidence differs per row, which is the reason
the column exists: a paper's own words for Casanovo 4.0.0 and 4.2.0, a Zenodo
record title for the two nine-species ones, the filename `model_massivekb.ckpt`
for RefineNovo-30M, and for π-PrimeNovo's phosphorylation model an **inherited**
link, since it was fine-tuned from `model_massive.ckpt` and its own fine-tuning
set, 2020-Cell-LUAD, is not in this catalog.

**Six of the eight assert no VERSION**, because the sources name the dataset and
not which of its versions, which is the same ambiguity the version list on the
page exists to expose. The page prints *not stated* rather than picking one.

The dataset pages show the table and then every evidence string beneath it. A
reader who disagrees with a link can see exactly what it rests on, which is the
point of not deriving them.
