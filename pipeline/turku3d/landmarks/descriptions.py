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


def resolve(building):
    key = str(building.facade_key)
    # Reviewed local measurements override distributed parameters without erasing them.
    osm = getattr(building, "attributes", {}).get("osm_id", "").replace("/", "_")
    paths = [
        ROOT / f"{key}.json",
        C.data_root() / "landmark-descriptions" / f"{osm}.json",
        C.data_root() / "landmark-descriptions" / f"{key}.json",
        C.work_root() / "landmark-descriptions" / f"{osm}.json",
        C.work_root() / "landmark-descriptions" / f"{key}.json",
    ]
    result = None
    for path in paths:
        if path.is_file():
            result = json.loads(path.read_text())
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
