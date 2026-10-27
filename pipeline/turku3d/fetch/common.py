"""Shared fetch helpers: resumable downloads, atomic writes, sha256 registry, polite HTTP.

Every raw kind (laz, wfs, ortho, osm) keeps two files at ``raw/<kind>/``:

* ``SHA256SUMS``   - ``<sha256>  <path relative to raw/<kind>>`` lines, sorted (``sha256sum -c`` compatible)
* ``MANIFEST.jsonl`` - one JSON record per file: path, url, bytes, sha256, fetched_at, http metadata

A download is *complete* when its final file exists and its size + sha256 match the registry; then a
re-run skips it (idempotent). Partial downloads live in ``<file>.part`` and are resumed with HTTP Range.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .. import config

USER_AGENT = "turku3d-pipeline/0.1 (+private research; contact wnt) Mozilla/5.0"
HTTP_TIMEOUT = 300
RETRIES = 5
CURL_RETRY, CURL_RETRY_DELAY = 8, 5  # curl --retry / --retry-delay (tests lower them)


def now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()


def sha256_file(path: Path, bufsize: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


# --------------------------------------------------------------------------------------
# Registry (SHA256SUMS + MANIFEST.jsonl per raw kind)
# --------------------------------------------------------------------------------------


def _remote(url: str | None) -> bool:
    return bool(url) and url.startswith(("https://", "http://"))


class Registry:
    """Checksum registry for one raw kind directory. Safe for concurrent processes (flock)."""

    def __init__(self, kind_dir: Path):
        self.dir = Path(kind_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sums = self.dir / "SHA256SUMS"
        self.manifest = self.dir / "MANIFEST.jsonl"
        self.lockfile = self.dir / ".registry.lock"

    @contextmanager
    def _locked(self):
        with open(self.lockfile, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def rel(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.dir.resolve()))

    def records(self) -> dict[str, dict]:
        if not self.manifest.exists():
            return {}
        st = self.manifest.stat()
        sig = (st.st_size, st.st_mtime_ns)
        if getattr(self, "_cache_sig", None) == sig:
            return self._cache
        out: dict[str, dict] = {}
        for line in self.manifest.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["path"]] = r  # last record wins
        self._cache, self._cache_sig = out, sig
        return out

    def get(self, path: Path) -> dict | None:
        return self.records().get(self.rel(path))

    def is_complete(self, path: Path, verify: bool = False) -> bool:
        """True if `path` exists and matches its registry record (size; sha256 too if verify)."""
        path = Path(path)
        if not path.exists():
            return False
        r = self.get(path)
        if not r or r.get("bytes") != path.stat().st_size:
            return False
        if verify:
            return sha256_file(path) == r["sha256"]
        return True

    def record(self, path: Path, url: str | None = None, sha256: str | None = None, **meta) -> dict:
        """Register a written file. A file downloaded from a remote source is only ever replaced
        by different content from a remote source: anything else is refused."""
        path = Path(path)
        sha256 = sha256 or sha256_file(path)
        prev = self.get(path)
        if prev and _remote(prev.get("url")) and prev["sha256"] != sha256 and not _remote(url):
            raise PermissionError(
                f"{self.rel(path)}: refusing to replace a file downloaded from {prev['url']} "
                f"with content from {url!r}"
            )
        rec = {
            "path": self.rel(path),
            "url": url,
            "bytes": path.stat().st_size,
            "sha256": sha256,
            "fetched_at": now_iso(),
            **meta,
        }
        with self._locked():
            with open(self.manifest, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self._rewrite_sums()
        return rec

    def forget(self, paths) -> None:
        """Drop the records of deleted files and of everything under deleted directories."""
        rels = [self.rel(p) for p in paths]
        with self._locked():
            if not self.manifest.exists():
                return
            lines = [
                line
                for line in self.manifest.read_text().splitlines()
                if line.strip()
                and not any(
                    (p := json.loads(line)["path"]) == r or p.startswith(r + "/") for r in rels
                )
            ]
            atomic_write_text(self.manifest, "".join(line + "\n" for line in lines))
            self._rewrite_sums()

    def _rewrite_sums(self) -> None:
        recs = self.records()
        lines = [f"{r['sha256']}  {p}" for p, r in sorted(recs.items()) if (self.dir / p).exists()]
        atomic_write_text(self.sums, "\n".join(lines) + ("\n" if lines else ""))

    def verify_all(self) -> list[str]:
        """Re-hash every registered file; return the list of mismatching/missing paths."""
        bad = []
        for p, r in sorted(self.records().items()):
            fp = self.dir / p
            if not fp.exists() or fp.stat().st_size != r["bytes"] or sha256_file(fp) != r["sha256"]:
                bad.append(p)
        return bad


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


def http_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = HTTP_TIMEOUT,
    retries: int = RETRIES,
) -> tuple[bytes, dict]:
    """GET with retries/backoff. Returns (body, lower-cased headers). Raises on final failure."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout
            ) as r:
                body = r.read()
                return body, {k.lower(): v for k, v in r.headers.items()}
        # http.client.HTTPException covers IncompleteRead (connection dropped mid-body), which is not an
        # OSError and used to escape the retry loop on the first truncated response.
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
        ) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                raise
            if attempt + 1 < retries:
                time.sleep(min(60, 2**attempt))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last}")


def curl_head(url: str) -> dict:
    """HEAD following redirects; returns final status, url, content-length, last-modified, etag."""
    out = subprocess.run(
        [
            "curl",
            "-sSIL",
            "--retry",
            "3",
            "-A",
            USER_AGENT,
            "-w",
            "\n__FINAL__ %{http_code} %{url_effective}\n",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # headers of the last response only
    blocks = [b for b in out.split("\r\n\r\n") if b.strip()]
    final = out.rsplit("__FINAL__", 1)[1].split()
    hdrs: dict[str, str] = {}
    for blk in blocks:
        if blk.lstrip().startswith("HTTP/"):
            hdrs = {}
            for line in blk.splitlines()[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.strip().lower()] = v.strip()
    return {
        "status": int(final[0]),
        "url": final[1] if len(final) > 1 else url,
        "content_length": int(hdrs["content-length"]) if "content-length" in hdrs else None,
        "last_modified": hdrs.get("last-modified"),
        "etag": hdrs.get("etag"),
    }


def curl_download(
    url: str,
    dest: Path,
    reg: Registry,
    expected_bytes: int | None = None,
    verify: bool = False,
    **meta,
) -> tuple[dict, bool]:
    """Resumable `curl -L -C -` download into dest (via dest.part), then sha256 + registry record.

    Returns (record, downloaded_now). Skips if already complete. If the server reports a size and the
    finished file differs, the .part is removed and an error raised (next run starts fresh).
    """
    dest = Path(dest)
    if reg.is_complete(dest, verify=verify):
        return reg.get(dest), False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_bytes is None:
        expected_bytes = curl_head(url)["content_length"]
    part = dest.with_name(dest.name + ".part")
    if dest.exists() and not part.exists():
        # final file without a registry record (e.g. interrupted after rename): re-validate by size
        if expected_bytes is not None and dest.stat().st_size == expected_bytes:
            return reg.record(dest, url=url, **meta), False
        os.replace(dest, part)
    if part.exists() and expected_bytes is not None and part.stat().st_size > expected_bytes:
        part.unlink()
    if not (part.exists() and expected_bytes is not None and part.stat().st_size == expected_bytes):
        base = [
            "curl",
            "-fL",
            "-sS",
            "--retry",
            str(CURL_RETRY),
            "--retry-delay",
            str(CURL_RETRY_DELAY),
            "--retry-all-errors",
            "-A",
            USER_AGENT,
            "-o",
            str(part),
            url,
        ]
        # Resume only when there is something to resume. curl exit 33 means the server ignored the Range
        # request: the .part cannot be trusted as a prefix, so restart from zero instead of accepting it
        # (with no Content-Length the truncated .part used to be recorded as complete).
        resume = part.exists() and part.stat().st_size > 0
        r = subprocess.run(base[:1] + ["-C", "-"] + base[1:] if resume else base)
        if r.returncode == 33:
            part.unlink(missing_ok=True)
            r = subprocess.run(base)
        if r.returncode != 0:
            raise RuntimeError(f"curl failed ({r.returncode}) for {url}; partial kept at {part}")
    got = part.stat().st_size
    if expected_bytes is not None and got != expected_bytes:
        part.unlink()
        raise RuntimeError(f"size mismatch for {url}: got {got}, expected {expected_bytes}")
    os.replace(part, dest)
    return reg.record(dest, url=url, **meta), True


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def kind_dir(kind: str) -> Path:
    d = config.raw_dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    return d
