"""Sandbox-local content-addressed pure stage results, independent of paths and git HEAD."""

from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import inspect
import json
import pickle
import struct
import tempfile
import time
from pathlib import Path

import numpy as np


def content_key(value) -> str:
    """Hash typed values, array layout and geometry bytes; mapping insertion order is irrelevant."""
    h = hashlib.sha256()

    def part(tag, data=b""):
        h.update(tag + struct.pack("<Q", len(data)) + data)

    def visit(v):
        if v is None:
            part(b"none")
        elif isinstance(v, (bool, str, int, float)):
            part(type(v).__name__.encode(), repr(v).encode())
        elif isinstance(v, bytes):
            part(b"bytes", v)
        elif isinstance(v, np.ndarray):
            if v.dtype.hasobject:
                raise TypeError("stage keys cannot contain object arrays")
            part(b"array", str((v.dtype.descr, v.shape)).encode())
            part(b"data", v.tobytes(order="C"))
        elif isinstance(v, np.generic):
            visit(np.asarray(v))
        elif isinstance(v, dict):
            part(b"dict", str(len(v)).encode())
            for key in sorted(v, key=content_key):
                visit(key)
                visit(v[key])
        elif isinstance(v, (list, tuple)):
            part(type(v).__name__.encode(), str(len(v)).encode())
            for item in v:
                visit(item)
        elif isinstance(v, (set, frozenset)):
            part(type(v).__name__.encode())
            visit(sorted(content_key(item) for item in v))
        elif hasattr(v, "wkb"):
            part(b"geometry", v.wkb)
        elif dataclasses.is_dataclass(v) or hasattr(v, "__dict__"):
            part(b"object", f"{type(v).__module__}.{type(v).__qualname__}".encode())
            visit(vars(v))
        else:
            raise TypeError(f"unsupported stage key type: {type(v)}")

    visit(value)
    return h.hexdigest()


def source_key(*sources) -> str:
    """Explicit transitive stage code/config dependencies; functions permit edits within a module."""
    return content_key(
        [
            source.read_bytes() if isinstance(source, Path) else inspect.getsource(source)
            for source in sources
        ]
    )


class StageCache:
    """Cache trusted local Python intermediates; checksum and atomic writes reject interrupted entries.

    The operation must have no side effects: mutable inputs it changes must be returned as outputs.
    Entries are immutable, so reverting a source edit reuses the older result too.
    """

    def __init__(self, root: Path):
        self.root = root
        self.metrics = {}

    def run(self, name, code, inputs, operation):
        started = time.perf_counter()
        key = content_key(["stage-v1", name, code, inputs])
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{key}.pkl"
        with (directory / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value, hit = None, False
            if target.exists():
                data = target.read_bytes()
                if len(data) >= 32 and hashlib.sha256(data[32:]).digest() == data[:32]:
                    try:
                        value, hit = pickle.loads(data[32:]), True
                    except (EOFError, pickle.UnpicklingError, AttributeError, ImportError):
                        pass
            if not hit:
                value = operation()
                data = pickle.dumps(value, protocol=5)
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(hashlib.sha256(data).digest() + data)
                temporary.replace(target)
        self.metrics[name] = dict(
            key=key, cached=hit, seconds=round(time.perf_counter() - started, 6)
        )
        return value

    def files(self, name, code, inputs, directory: Path, operation):
        """Cache a stage's local files together with its typed result; no path enters the key."""

        def build():
            with tempfile.TemporaryDirectory(prefix="stage-", dir=self.root) as temp:
                root = Path(temp)
                value = operation(root)
                files = {
                    str(p.relative_to(root)): p.read_bytes()
                    for p in sorted(root.rglob("*"))
                    if p.is_file()
                }
                return value, files

        # run() creates the namespace before invoking build().
        value, files = self.run(name, code, inputs, build)
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        return value

    def report(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.metrics, indent=2) + "\n")
