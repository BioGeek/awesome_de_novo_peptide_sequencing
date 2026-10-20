import zipfile, sys, collections
for z in sys.argv[1:]:
    zf = zipfile.ZipFile(z)
    for m in zf.namelist():
        if not m.endswith(".mgf") or not any(s in m for s in ("Mus-musculus", "H.-sapiens", "Candidatus")): continue
        c = collections.Counter(); seqs_in_rec = collections.Counter(); n = 0
        with zf.open(m) as f:
            for line in f:
                if line.startswith(b"BEGIN IONS"): c["begin"] += 1; n = 0
                elif line.startswith(b"SEQ="): c["seq"] += 1; n += 1
                elif line.startswith(b"SCANS="): c["scans"] += 1
                elif line.startswith(b"END IONS"): seqs_in_rec[n] += 1
        print(z.split("/")[-1], m.split("/")[-1], dict(c), "SEQ lines per record:", dict(seqs_in_rec))
