"""Versioned, deterministic transforms of finished coarse tile products.

A migration edits one finished tile product (its manifest and payloads). The generator applies
the registered chain at the end of every build (``turku3d.coarse.build``); the farm applies the
same functions to already built products (``scripts/dev/tile_migrate.py``). A regeneration
therefore equals base + migrations.

Payload writes are content addressed: every written file name carries the SHA-256 of its bytes
and an existing file is never modified. Unchanged payloads of a migrated product are hard links
to the base product, so a no-op migration costs almost no disk.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

PACKAGE = Path(__file__).resolve().parents[1]  # pipeline/turku3d
HASH = re.compile(r"[a-f0-9]{64}")
NAME = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
# Farm bookkeeping beside the payloads: linked into a migrated product, never rewritten.
AUX = ("stage-times.json", "input-cache.json")


@dataclass(frozen=True)
class Migration:
    """``transform(product)`` edits ``product.manifest`` and writes payloads through ``product``.

    Bump ``version`` whenever the output changes. The transform must be deterministic: the same
    input product always gives the same bytes. It may read only the product (and, when needed,
    source data through the pipeline's own loaders)."""

    name: str
    version: int
    transform: Callable[["Product"], None]

    def __post_init__(self):
        if not NAME.fullmatch(self.name) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"invalid migration {self.name}@{self.version}")

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def code_hash(self) -> str:
        """Source of the transform's module, this framework and every pipeline module they import."""
        module = self.transform.__module__
        if module.split(".")[0] == "turku3d":
            source = module_file(module.split("."))
        else:  # A test's own migration; registered migrations live in turku3d.migrations.
            source = Path(sys.modules[module].__file__).resolve()
        digest = hashlib.sha256()
        for path in closure([source, Path(__file__).resolve()]):
            name = path.relative_to(PACKAGE).as_posix() if path.is_relative_to(PACKAGE) else path.name
            digest.update(name.encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()


def module_file(parts: list[str]) -> Path:
    if parts[0] != "turku3d":
        raise ValueError(f"migration transforms live in the turku3d package: {'.'.join(parts)}")
    base = PACKAGE.joinpath(*parts[1:])
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(".".join(parts))


def _resolve(parts: list[str]) -> list[Path]:
    """Files executed by importing ``turku3d.<parts>``: package __init__s and the module."""
    found = [(PACKAGE / "__init__.py").resolve()] if (PACKAGE / "__init__.py").is_file() else []
    for n in range(1, len(parts) + 1):
        base = PACKAGE.joinpath(*parts[:n])
        for candidate in (base / "__init__.py", base.with_suffix(".py")):
            if candidate.is_file():
                found.append(candidate.resolve())
                break
    return found


# The chain order is part of every generation id; appending a migration to the registry must
# not change the identity of the migrations before it.
REGISTRY = PACKAGE / "migrations" / "registry.py"


def closure(roots: Iterable[Path]) -> list[Path]:
    """Static import closure inside ``pipeline/turku3d`` (lazy imports included)."""
    seen, todo = set(), list(roots)
    while todo:
        path = todo.pop()
        if path in seen or path == REGISTRY:
            continue
        seen.add(path)
        # An __init__'s package is its directory; files outside the package import absolutely.
        package = path.relative_to(PACKAGE).parent.parts if path.is_relative_to(PACKAGE) else ()
        for node in ast.walk(ast.parse(path.read_bytes())):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name.split(".") for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = list(package[: len(package) - node.level + 1])
                    base = anchor + (node.module.split(".") if node.module else [])
                else:
                    base = node.module.split(".") if node.module else []
                    if base[:1] != ["turku3d"]:
                        continue
                    base = base[1:]
                targets = [["turku3d", *base]] + [["turku3d", *base, a.name] for a in node.names]
            for target in targets:
                if target[:1] == ["turku3d"]:
                    todo.extend(_resolve(target[1:]))
    return sorted(seen)


def step_generation(parent: str, migration: Migration) -> str:
    """Generation id after one migration: parent generation, name@version and code hash."""
    text = f"turku3d-tile-migration-v1\n{parent}\n{migration.key}\n{migration.code_hash()}"
    return hashlib.sha256(text.encode()).hexdigest()


def chain_generations(base: str, chain: Iterable[Migration]) -> list[str]:
    ids, parent = [], base
    for migration in chain:
        parent = step_generation(parent, migration)
        ids.append(parent)
    return ids


def glb_chunks(data: bytes) -> tuple[dict, bytes]:
    """(JSON, BIN) chunks of a GLB v2."""
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2 or length != len(data):
        raise ValueError("not a GLB v2")
    size, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise ValueError("GLB has no JSON chunk")
    document = json.loads(data[20 : 20 + size])
    offset = 20 + size
    if offset + 8 > len(data):
        return document, b""
    size, _ = struct.unpack_from("<II", data, offset)
    return document, data[offset + 8 : offset + 8 + size]


def glb_pack(document: dict, binary: bytes = b"") -> bytes:
    """Deterministic GLB v2: compact JSON padded with spaces, BIN padded with zeros."""
    text = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    text += b" " * (-len(text) % 4)
    binary = binary + b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def _normal(url: str) -> str:
    path = posixpath.normpath(url)
    if path.startswith("/") or (path.startswith("..") and not path.startswith("../shared/")):
        raise ValueError(f"payload outside the tile or shared store: {url}")
    return path


class Product:
    """One tile product during a migration. Reads see earlier writes of the same chain."""

    def __init__(self, directory: Path, manifest: dict):
        self.directory = Path(directory)
        self.manifest = manifest
        self.tile = manifest["tile"]
        self.written: dict[str, bytes] = {}  # normalised path relative to the tile directory
        self.replaced: set[str] = set()

    def read(self, url: str) -> bytes:
        path = _normal(url)
        if path in self.written:
            return self.written[path]
        return (self.directory / path).read_bytes()

    def glb_json(self, url: str) -> dict:
        """A GLB's JSON chunk without reading its binary chunk from disk."""
        path = _normal(url)
        if path in self.written:
            return glb_chunks(self.written[path])[0]
        with (self.directory / path).open("rb") as stream:
            head = stream.read(20)
            magic, version, _ = struct.unpack_from("<III", head, 0)
            size, kind = struct.unpack_from("<II", head, 12)
            if magic != 0x46546C67 or version != 2 or kind != 0x4E4F534A:
                raise ValueError(f"not a GLB v2 with a JSON chunk: {url}")
            return json.loads(stream.read(size))

    def put(self, pattern: str, data: bytes) -> str:
        """Write ``data`` under ``pattern`` with ``{hash}`` replaced by its SHA-256; return the url.

        Tile payloads use e.g. ``vis.{hash}.glb`` or ``col/c_0_0.{hash}.col``; shared assets
        ``../shared/{hash}.glb``."""
        if pattern.count("{hash}") != 1:
            raise ValueError("payload names carry exactly one {hash}")
        url = pattern.replace("{hash}", hashlib.sha256(data).hexdigest())
        self.written[_normal(url)] = bytes(data)
        return url

    def replace(self, entry: dict, data: bytes) -> str:
        """Replace a manifest entry's payload (``url``/``uri`` with its hash in the name).

        Updates ``sha256``/``id`` and ``bytes``. Other derived numbers in the entry (``tris``,
        ``brotli_bytes``, ``h``) are the transform's to update."""
        field = "url" if "url" in entry else "uri"
        old = entry[field]
        found = HASH.findall(posixpath.basename(old))
        if len(found) != 1:
            raise ValueError(f"payload name has no single content hash: {old}")
        digest = hashlib.sha256(data).hexdigest()
        if digest == found[0]:
            return old  # Byte-identical: keep the existing (linked) payload.
        head, name = posixpath.split(old)
        url = posixpath.join(head, name.replace(found[0], digest))
        self.written[_normal(url)] = bytes(data)
        self.replaced.add(_normal(old))
        entry[field] = url
        if "sha256" in entry:
            entry["sha256"] = digest
        if entry.get("id") == found[0]:
            entry["id"] = digest
        if "bytes" in entry:
            entry["bytes"] = len(data)
        return url


def references(product: Product) -> list[str]:
    """Normalised paths of every payload the manifest reaches, including GLB external URIs."""
    m = product.manifest
    entries = [*m.get("visual", {}).values(), *m.get("collision", {}).values(),
               *m.get("vehicle_models", {}).values()]
    if m.get("roads"):
        entries.append(m["roads"])
    urls = [e["url"] for e in entries]
    urls += [model["uri"] for model in m.get("buildingPlacements", {}).get("models", [])]
    seen, order, todo = set(), [], [_normal(u) for u in urls]
    while todo:
        path = todo.pop(0)
        if path in seen:
            continue
        seen.add(path)
        order.append(path)
        if path.endswith(".glb"):
            document = product.glb_json(path)
            for item in [*document.get("images", []), *document.get("buffers", [])]:
                uri = item.get("uri")
                if uri and not uri.startswith("data:"):
                    todo.append(_normal(posixpath.join(posixpath.dirname(path), uri)))
    return order


def manifest_bytes(manifest: dict) -> bytes:
    """The generator's manifest serialisation."""
    return (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()


def normalise(manifest: dict) -> dict:
    """The manifest as a reader of manifest.json sees it (key order, tuples as lists)."""
    return json.loads(manifest_bytes(manifest))


def run(product: Product, chain: Iterable[Migration]) -> list[dict]:
    """Apply ``chain`` and append it to the manifest's ``migrations`` record."""
    done = list(product.manifest.get("migrations", []))
    for migration in chain:
        migration.transform(product)
        done.append(dict(name=migration.name, version=migration.version))
    product.manifest["migrations"] = done
    return done


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(data)
    temp.replace(path)


def _publish(path: Path, data: bytes | None, source: Path | None) -> None:
    """Content-addressed publish: an existing file already holds these bytes."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        try:
            os.link(source, path)
        except FileExistsError:
            pass
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(data)
    try:
        os.link(temp, path)  # Never replaces a concurrent writer's identical file.
    except FileExistsError:
        pass
    finally:
        temp.unlink()


def write_in_place(product: Product) -> None:
    """Generator path: new payloads beside the build's own; replaced tile payloads removed."""
    for path, data in product.written.items():
        _publish(product.directory / path, data, None)
    live = set(references(product))
    for path in product.replaced - live:
        if not path.startswith("../"):
            (product.directory / path).unlink(missing_ok=True)


def materialize(product: Product, target: Path) -> list[str]:
    """Farm path: build ``target`` (a new tile directory) from hard links and written payloads.

    Shared assets go to ``target/../shared``. Returns the referenced payload paths."""
    target.mkdir(parents=True)
    paths = references(product)
    for path in paths:
        _publish(target / path, product.written.get(path),
                 None if path in product.written else product.directory / path)
    for name in AUX:
        if (product.directory / name).is_file():
            os.link(product.directory / name, target / name)
    _atomic(target / "manifest.json", manifest_bytes(product.manifest))
    return paths
