"""The two roots of the world pipeline (docs/design/world-pipeline.md, section 4).

Output store: immutable content-addressed objects and build records, plus the mutable location manifests.
Shared data: stored descriptions (logged, history kept) and the cache of fetched source data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .. import config
from .api import ENVELOPE, TIERS

ID = re.compile(r"^[a-z][a-z0-9-]*/[A-Za-z0-9._-]+$")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    os.replace(tmp, path)


def data_root() -> Path:
    return Path(os.environ.get("TURKU3D_WORLD_DATA") or config.data_root() / "world")


def description_roots() -> list[Path]:
    roots = os.environ.get("TURKU3D_WORLD_DESCRIPTIONS")
    return [Path(p) for p in roots.split(":") if p] if roots else [data_root() / "descriptions"]


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("TURKU3D_WORLD_STORE") or config.work_root() / "world")

    # ---------------------------------------------------------------- immutable objects
    def put(self, data: bytes, ext: str) -> dict:
        """Stores a blob under its hash; returns its {url, sha256, bytes}."""
        digest = sha256(data)
        url = f"objects/{digest[:2]}/{digest}.{ext}"
        path = self.root / url
        if not path.exists():
            write_atomic(path, data)
        return {"url": url, "sha256": digest, "bytes": len(data)}

    def put_json(self, value) -> dict:
        return self.put(canonical(value), "json")

    def get_json(self, url: str):
        return json.loads((self.root / url).read_bytes())

    def record(self, key: str) -> dict | None:
        path = self.root / "builds" / f"{key}.json"
        return json.loads(path.read_bytes()) if path.exists() else None

    def put_record(self, key: str, record: dict) -> None:
        write_atomic(self.root / "builds" / f"{key}.json", canonical(record))

    # ---------------------------------------------------------------- source facts of the last discovery
    def facts_path(self, entity: str) -> Path:
        return self.root / "facts" / f"{entity}.json"

    def put_facts(self, entity: str, stub: dict) -> None:
        data = canonical(stub)
        path = self.facts_path(entity)
        if not path.exists() or path.read_bytes() != data:
            write_atomic(path, data)

    def facts(self, entity: str) -> dict:
        return json.loads(self.facts_path(entity).read_bytes())

    # ---------------------------------------------------------------- location manifests
    def location_path(self, name: str) -> Path:
        """`locations/NAME.json`; a region's build unit `REGION/UNIT` is `units/REGION/UNIT.json`, a manifest the
        lab does not list (docs/design/world-regions.md)."""
        return self.root / ("units" if "/" in name else "locations") / f"{name}.json"

    def location(self, name: str) -> dict | None:
        path = self.location_path(name)
        return json.loads(path.read_bytes()) if path.exists() else None

    def building_path(self, name: str) -> Path:
        return self.root / "building" / f"{name}.json"

    def builds_running(self) -> list[dict]:
        """The markers of builds of this store that still run (a marker of this machine whose process is gone is not one)."""
        running = []
        for path in sorted((self.root / "building").rglob("*.json")):
            marker = json.loads(path.read_text())
            if marker.get("host") == socket.gethostname():
                try:
                    os.kill(int(marker["pid"]), 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    pass
            running.append(marker)
        return running

    @contextmanager
    def building(self, name: str):
        """Marks a location as being built while the block runs: a capture of it is refused meanwhile, because a
        page follows its manifest and a new revision would redraw it under the camera."""
        path = self.building_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name, "host": socket.gethostname(), "pid": os.getpid(),
                                    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    def put_location(self, manifest: dict) -> None:
        write_atomic(self.location_path(manifest["name"]), json.dumps(manifest, separators=(",", ":")).encode())

    def referenced(self) -> set[str]:
        """Object urls that any location manifest of this store draws (a region's rows are its units' manifests)."""
        urls: set[str] = set()
        for path in [*(self.root / "locations").glob("*.json"), *(self.root / "units").glob("*/*.json")]:
            manifest = json.loads(path.read_bytes())
            if "entities" not in manifest:  # a region index or a region's plan: its chunks repeat unit rows
                urls.update(chunk["url"] for chunk in manifest.get("chunks", {}).values())
                urls.update(cell["url"] for cell in manifest.get("collision", {}).values())
                urls.update(tile["url"] for tile in manifest.get("roads", {}).values())
                continue
            urls.update(cell["url"] for cell in manifest.get("collision", {}).values())
            urls.update(tile["url"] for tile in manifest.get("roads", {}).values())
            for row in manifest["entities"]:
                # The level a row draws and the ones it lists for a client that chooses by distance.
                for level in (row, *row.get("levels", [])):
                    if level.get("url"):
                        urls.add(level["url"])
                    urls.update(i["asset"] for i in level.get("instances", []))
                urls.update(row.get("images", []))
        return urls


# -------------------------------------------------------------------- stored descriptions


def description_path(entity: str, root: Path | None = None) -> Path:
    if not ID.match(entity):
        raise ValueError(f"bad entity id: {entity}")
    return (root or description_roots()[0]) / f"{entity}.json"


# Every stored description this process has read, by entity id: {tier, sha256}. A build writes the list into its
# manifest, so that a judged score can say without doubt whether a vision reading was in it.
READ: dict[str, dict] = {}


def read_description(entity: str) -> dict | None:
    """The stored description of an entity: the first description root that has one."""
    for root in description_roots():
        path = description_path(entity, root)
        if path.exists():
            data = path.read_bytes()
            description = json.loads(data)
            note_read({**description, "id": entity}, data)
            return description
    return None


def note_read(description: dict, data: bytes) -> None:
    """Records a stored description that was read some other way than by `read_description` (a location's
    reading found by place, world/profile.py)."""
    READ[description["id"]] = {"tier": description.get("tier", "auto"), "sha256": sha256(data)}


def descriptions_used(roles: dict[str, str] | None = None) -> dict:
    """What `READ` holds, for a manifest: the roots, every description read with its tier and (for an entity of
    the location) its role, the counts by tier, and `stage`: "cold" when nothing above the automatic tier was
    read, else "characterised"."""
    used = [{"id": entity, **entry, **({"role": roles[entity]} if roles and entity in roles else {})}
            for entity, entry in sorted(READ.items())]
    tiers: dict[str, int] = {}
    for row in used:
        tiers[row["tier"]] = tiers.get(row["tier"], 0) + 1
    return {"roots": [str(root) for root in description_roots()], "used": used, "byTier": tiers,
            "stage": "characterised" if any(tier != "auto" for tier in tiers) else "cold"}


def write_description(description: dict, by: str, note: str = "") -> Path:
    """Stores a description: validates the envelope, never lowers a tier, keeps the old version, logs the write."""
    missing = [k for k in ENVELOPE if k not in description]
    if missing:
        raise ValueError(f"description lacks {missing}")
    entity, tier = description["id"], description["tier"]
    kind = entity.split("/", 1)[0]
    if not description["schema"].startswith(f"turku-description/{kind}/"):
        raise ValueError(f"schema {description['schema']} is not a {kind} description")
    if tier not in TIERS:
        raise ValueError(f"bad tier {tier}")
    root = description_roots()[0]
    path = description_path(entity, root)
    old = json.loads(path.read_bytes()) if path.exists() else None
    if old and TIERS.index(tier) < TIERS.index(old["tier"]):
        raise ValueError(f"{entity}: stored tier {old['tier']} is not replaced by {tier}")
    data = json.dumps(description, indent=1, sort_keys=True, ensure_ascii=False).encode()
    digest = sha256(data)
    write_atomic(root / ".history" / f"{digest}.json", data)
    write_atomic(path, data)
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": by, "tier": tier, "sha256": digest, "note": note}
    with open(path.with_suffix(".log.jsonl"), "a") as log:
        log.write(json.dumps(line) + "\n")
    return path
