"""Building-owned, image-measured landmark parameters; references remain local."""

import hashlib
import json
from pathlib import Path

from turku3d import config as C

ROOT = Path(__file__).with_name("descriptions")


def source_paths():
    return [
        Path(__file__).with_name(name)
        for name in ("appearance.py", "atlas.py", "descriptions.py", "procedural.py", "elevations.py", "relief.py", "openings.py")
    ] + [C.REPO_ROOT / "textures/turkutex/facade_archetypes.py"]


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


def described_refs(refs):
    """Whether any of a manifest's ``landmark_buildings`` refs ([facade key, OSM id] pairs) has a
    landmark description: only then does the landmark appearance code shape that tile's buildings."""
    from types import SimpleNamespace

    return any(
        resolve(SimpleNamespace(facade_key=key, attributes={"osm_id": osm})) is not None
        for key, osm in refs
    )


def manifest_current(manifest):
    return "landmark_buildings" not in manifest or manifest.get("landmark_params") == fingerprint(
        manifest["landmark_buildings"]
    )


def measured_class(building):
    """Reference-measured facade classes outrank unreviewed automatic seeds.

    Precedence: local override > reviewed distributed description > measured class
    (massive masonry, a public monolith with photographed blank walls, a classical
    order named by the building's own reviewed walls or vision description, or a
    reviewed modernist storey profile on its own walls) > automatic
    description > generic archetype.
    """
    from turku3d.facade_kit import read_description, renderer

    renderer()
    from turkutex.facade_archetypes import classical_order, massive_masonry, public_monolith

    from turku3d import facade_monolith
    from turku3d.facade_programs import measured, order_evidence

    attrs = getattr(building, "attributes", {}) or {}
    if massive_masonry(attrs):
        return True
    # A reviewed modernist storey profile on the building's own walls (vfix 029/041).
    program = measured(building.facade_key) or {}
    if any(w.get("observation", {}).get("dimension_source") == "reviewed modernist storey profile"
           for w in program.get("walls", [])):
        return True
    footprint = getattr(building, "footprint", None)
    area = float(getattr(footprint, "area", 0) or attrs.get("footprint_m2") or 0)
    if public_monolith({**attrs, "footprint_m2": area, "monolith_blank": True}) and (
            facade_monolith.evidence(building.facade_key, measured(building.facade_key))["blank"]):
        return True
    evidence = order_evidence(measured(building.facade_key), read_description(building.facade_key))
    return "pilaster" in evidence.lower() and classical_order(attrs, evidence)


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
                or measured_class(building)
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
