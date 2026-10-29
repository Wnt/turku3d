"""Relocate retained inventory pointers without changing immutable score evidence."""

import json
from pathlib import Path


def relocate(value, root):
    """Translate old sandbox pointers only when the durable target actually exists."""
    if isinstance(value, dict):
        return {key: relocate(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [relocate(item, root) for item in value]
    if isinstance(value, str) and value.startswith("/") and "/gmaps-round3/" in value:
        suffix = value.split("/gmaps-round3/", 1)[1]
        if ".." in Path(suffix).parts:
            return value
        destination = Path(root) / suffix
        if destination.resolve().is_relative_to(Path(root).resolve()) and destination.exists():
            return str(destination)
    return value


def restore_pointers(root):
    """Keep reports/manifests byte-identical: their hashes are part of paired evidence."""
    from storage import atomic_json

    root = Path(root)
    paths = [*root.glob("synthetic/*coverage.json"), root / "synthetic/proof.json"]
    paths += list(root.glob("synthetic/captures/*/capture.json"))
    paths += list(root.glob("synthetic/captures/*/oci-snapshot.json"))
    changed = 0
    for path in paths:
        if not path.exists():
            continue
        original = json.loads(path.read_text())
        restored = relocate(original, root)
        if restored != original:
            atomic_json(path, restored)
            changed += 1
    return changed
