"""Building-owned, image-measured landmark parameters; references remain local."""

import hashlib
import json
from pathlib import Path

from turku3d import config as C

ROOT = Path(__file__).with_name("descriptions")


def source_paths():
    return [
        Path(__file__).with_name(name)
        for name in (
            "appearance.py",
            "atlas.py",
            "descriptions.py",
            "faces.py",
            "layout.py",
            "procedural.py",
            "relief.py",
        )
    ]


def building_refs(buildings):
    return sorted(
        [[str(b.facade_key), getattr(b, "attributes", {}).get("osm_id", "")] for b in buildings]
    )


def fingerprint(refs):
    from types import SimpleNamespace

    values = [
        (key, resolve(SimpleNamespace(facade_key=key, attributes={"osm_id": osm})))
        for key, osm in sorted(refs)
    ]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def parameter_hash(buildings):
    return fingerprint(building_refs(buildings))


def manifest_current(manifest):
    return "landmark_buildings" not in manifest or manifest.get("landmark_params") == fingerprint(
        manifest["landmark_buildings"]
    )


def resolve(building, automatic_root=None):
    key = str(building.facade_key)
    automatic_root = automatic_root or C.data_root() / "landmark-descriptions"
    # Automatically estimated parameters seed new landmarks. Reviewed distributed
    # parameters win until a deliberate local development override is supplied.
    osm = getattr(building, "attributes", {}).get("osm_id", "").replace("/", "_")
    paths = [
        automatic_root / f"{osm}.json",
        automatic_root / f"{key}.json",
        ROOT / f"{key}.json",
        C.work_root() / "landmark-descriptions" / f"{osm}.json",
        C.work_root() / "landmark-descriptions" / f"{key}.json",
    ]
    result = None
    for path in paths:
        if path.is_file():
            candidate = json.loads(path.read_text())
            if path.parent == automatic_root and (
                candidate.get("reference_match") != "verified"
                or candidate.get("reference_confidence", 0) < 0.5
            ):
                continue
            result = candidate
    if result is not None:
        if (
            result.get("building_id")
            not in (key, getattr(building, "attributes", {}).get("osm_id"))
            or result.get("version") != 1
        ):
            raise ValueError(f"invalid landmark description for {key}")
        result["content_hash"] = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
    return result
