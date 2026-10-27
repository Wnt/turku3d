"""Read a consistent ground-layer snapshot without writing to its producer sandbox."""

import hashlib
import json
from pathlib import Path

from turku3d import config as C
from turku3d.fetch.common import atomic_write_bytes


def snapshot_ground(source):
    source = Path(source).resolve()
    manifest_path = source / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"W1-01 ground layer is required: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    paths = (
        [source / name for name in sorted(manifest["tiles"]) if name.endswith(".col.npz")]
        if manifest.get("format") == "turku3d-layer"
        else sorted(source.glob("*.col.npz"))
    )
    if (source / "quays.json").is_file():
        paths.append(source / "quays.json")
    if (source / "ramps.json").is_file():
        paths.append(source / "ramps.json")
    if not paths:
        raise ValueError("ground snapshot has no collision tiles")
    contents = {p.name: p.read_bytes() for p in paths}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    # The producer replaces its manifest last. Reject a mixed generation and let
    # the next build retry, rather than measuring a half-written set of tiles.
    declared = dict(manifest.get("artifacts", manifest.get("files", {})))
    if manifest.get("format") == "turku3d-layer":
        declared.update({name: entry["sha256"] for name, entry in manifest["tiles"].items()})
    mismatches = [name for name, digest in hashes.items() if declared.get(name, digest) != digest]
    if mismatches:
        raise ValueError(f"ground snapshot differs from its manifest: {mismatches}")
    if manifest_path.read_bytes() != manifest_bytes or any(
        hashlib.sha256(p.read_bytes()).hexdigest() != hashes[p.name] for p in paths
    ):
        raise ValueError("ground layer changed during snapshot; rerun after the producer completes")
    digest = hashlib.sha256(
        manifest_bytes + json.dumps(hashes, sort_keys=True).encode()
    ).hexdigest()
    root = C.cache_dir("structures-ground") / digest
    root.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        atomic_write_bytes(root / name, data)
    atomic_write_bytes(root / "MANIFEST.json", manifest_bytes)
    evidence = {
        "source": str(source),
        "snapshot": str(root),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "code_version": manifest.get(
            "code", manifest.get("code_version", manifest.get("code_hash"))
        ),
        "ground_acceptance_passed": manifest.get("acceptance_passed"),
        "files": hashes,
        "manifest_hash_mismatches": [],
        "changed_during_read": [],
    }
    atomic_write_bytes(root / "snapshot.json", (json.dumps(evidence, indent=2) + "\n").encode())
    return root, evidence
