"""Recursively list a MassIVE deposit over FTPS: path, size. usage: massive_ls.py MSV000081382"""
import ftplib, sys
acc = sys.argv[1]
ftp = ftplib.FTP_TLS("massive-ftp.ucsd.edu", timeout=120)
ftp.login(); ftp.prot_p()
root = None
for v in [f"v{i:02d}" for i in range(1, 12)]:
    try:
        ftp.cwd(f"/{v}/{acc}"); root = f"/{v}/{acc}"; break
    except ftplib.error_perm:
        continue
if not root:
    sys.exit(f"{acc}: not found under v01-v11")
def walk(path):
    entries = []
    ftp.retrlines(f"LIST {path}", entries.append)
    for e in entries:
        parts = e.split(None, 8)
        if len(parts) < 9: continue
        name = parts[8].split(" -> ")[0]
        if name in (".", ".."): continue
        full = f"{path}/{name}"
        if e.startswith("d"):
            walk(full)
        elif e.startswith("l"):
            print(f"LINK\t{full}\t{parts[8]}")
        else:
            print(f"{parts[4]}\t{full}")
walk(root)
