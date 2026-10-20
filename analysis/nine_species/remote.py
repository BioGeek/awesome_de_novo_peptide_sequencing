"""Seekable HTTP file over Range requests, so zipfile/pyarrow can read a remote archive's
directory or a parquet footer without downloading the whole thing."""
import io, requests, time
class HTTPFile(io.RawIOBase):
    def __init__(self, url, block=1 << 20):
        self.url, self.pos, self.block = url, 0, block
        r = requests.head(url, allow_redirects=True, timeout=60); r.raise_for_status()
        self.url = r.url
        self.size = int(r.headers["Content-Length"])
        self.cache = {}
        self.s = requests.Session()
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos
    def _get(self, start, end):
        for attempt in range(6):
            try:
                r = self.s.get(self.url, headers={"Range": f"bytes={start}-{end - 1}"}, timeout=300)
                if r.status_code == 206: return r.content
                raise IOError(f"HTTP {r.status_code}")
            except Exception:
                if attempt == 5: raise
                time.sleep(2 ** attempt)
    def readinto(self, b):
        if self.pos >= self.size: return 0
        n = min(len(b), self.size - self.pos)
        data = self._get(self.pos, self.pos + n) if n > self.block else self._cached(self.pos, n)
        b[:len(data)] = data; self.pos += len(data); return len(data)
    def _cached(self, pos, n):
        out = bytearray()
        while n > 0:
            k = pos // self.block
            if k not in self.cache:
                if len(self.cache) > 64: self.cache.clear()
                self.cache[k] = self._get(k * self.block, min(self.size, (k + 1) * self.block))
            chunk = self.cache[k][pos - k * self.block: pos - k * self.block + n]
            out += chunk; pos += len(chunk); n -= len(chunk)
        return bytes(out)
