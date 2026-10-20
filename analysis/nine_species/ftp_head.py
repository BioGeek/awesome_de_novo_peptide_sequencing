"""Print the first N bytes of MassIVE FTPS files. usage: ftp_head.py NBYTES path [path...]"""
import ftplib, sys
n = int(sys.argv[1])
class Enough(Exception): pass
for path in sys.argv[2:]:
    for attempt in range(3):
        try:
            ftp = ftplib.FTP_TLS("massive-ftp.ucsd.edu", timeout=120); ftp.login(); ftp.prot_p()
            buf = bytearray()
            def cb(b):
                buf.extend(b)
                if len(buf) >= n: raise Enough
            try: ftp.retrbinary(f"RETR {path}", cb, blocksize=8192)
            except Enough: pass
            try: ftp.close()
            except Exception: pass
            print(f"########## {path}\n" + buf[:n].decode("utf-8", "replace"))
            break
        except Enough: pass
        except Exception as e:
            if attempt == 2: print(f"########## {path}: {e}")
