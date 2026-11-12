"""Checks one mesh and one location against docs/design/render-readiness.md and names the rules they miss."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from . import field, measure
from .glbfile import Primitive, read

REPO = Path(__file__).resolve().parents[4]
REGISTRY = Path(__file__).resolve().parents[1] / "families.json"
OWNERS = {
    "building": "w2-buildings",
    "ground": "w2-street",
    "terrain": "w2-street",
    "wall": "w2-street",
    "water": "w2-street",
    "trees": "w2-green",
    "planting": "w2-green",
    "signpost": "w2-objects",
    "signal": "w2-objects",
    "lamp": "w2-objects",
    "pole": "w2-objects",
    "fence": "w2-objects",
    "furniture": "w2-objects",
}
HEIGHT_FIELDS = ("ground", "terrain")  # kinds that are open by design (F3)
FOLIAGE = ("trees", "planting")
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def registry() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"families": {}, "common": {}}


def materials_of(primitives: list[Primitive], known: dict) -> dict:
    """Material rows, families and how the surface area divides between opaque, masked, blend and decal."""
    rows: dict[str, Primitive] = {}
    by_surface: dict[str, float] = {}
    two_sided_opaque = emissive = 0.0
    for primitive in primitives:
        area = float(measure.areas(primitive.positions, primitive.triangles)[0].sum())
        rows.setdefault(primitive.material.key, primitive)
        by_surface[primitive.material.surface] = (
            by_surface.get(primitive.material.surface, 0.0) + area
        )
        if primitive.material.double_sided and primitive.material.surface == "opaque":
            two_sided_opaque += area
        if any(primitive.material.emissive):
            emissive += area
    unknown_families, unknown_params, nested, placeholder = set(), set(), set(), 0
    for row in rows.values():
        material = row.material
        declared = known["families"].get(material.family)
        if declared is None:
            unknown_families.add(material.family)
        for name, value in material.params.items():
            if isinstance(value, (dict, list)):
                nested.add(f"{material.family}.{name}")
            if (
                declared
                and declared.get("params") is not None
                and name not in declared["params"]
                and name not in known.get("common", {})
            ):
                unknown_params.add(f"{material.family}.{name}")
        if (
            material.family != "flat"
            and np.allclose(material.color, (0.6, 0.6, 0.6), atol=2e-3)
            and abs(material.roughness - 0.9) < 1e-3
        ):
            placeholder += 1
    total = sum(by_surface.values()) or 1.0
    return {
        "rows": len(rows),
        "families": sorted({row.material.family for row in rows.values()}),
        "unknown_families": sorted(unknown_families),
        "unknown_params": sorted(unknown_params),
        "nested_params": sorted(nested),
        "placeholder_fallback": placeholder,
        "surface_share": {
            name: round(area / total, 4) for name, area in sorted(by_surface.items())
        },
        "two_sided_opaque_m2": round(two_sided_opaque, 3),
        "emissive_m2": round(emissive, 3),
        "keys": sorted(rows),
    }


def check_mesh(
    primitives: list[Primitive], kind: str, known: dict | None = None, rays: int = 256
) -> tuple[dict, tuple | None]:
    """(report, welded mesh for the hierarchy builder). `kind` selects the rules: a ground cell is a height field,
    foliage may be masked."""
    known = known or registry()
    positions, triangles, _ = measure.assemble(primitives)
    report: dict = {
        "parts": sorted({p.part for p in primitives}),
        "materials": materials_of(primitives, known),
    }
    flags: list[str] = []
    if not len(triangles):
        return {**report, "triangles": 0, "flags": []}, None
    # Topology, solids and the field are judged on what is opaque: masked cards and blended slabs are not part of a
    # distance field or a surface cache, so a wall closed only by them is open (F2).
    solid_positions, solid_triangles, _ = measure.assemble(primitives, ("opaque",))
    everything = measure.topology(positions, triangles)
    topology = (
        measure.topology(solid_positions, solid_triangles) if len(solid_triangles) else everything
    )
    points, welded = everything.pop("welded")
    topology.pop("welded", None)
    for name in ("kept", "labels"):
        everything.pop(name, None)
        topology.pop(name, None)
    solids = topology.pop("solids")
    everything.pop("solids", None)
    closed = [s for s in solids if s["closed"]]
    area = topology["area_m2"] or 1.0
    thickness = [s["thickness_m"] for s in closed if s["thickness_m"] is not None]
    report.update(
        triangles=everything["triangles"],
        vertices=everything["vertices"],
        area_m2=round(everything["area_m2"], 3),
        density_per_m2=round(everything["triangles"] / max(everything["area_m2"], 1e-9), 2),
        small_share=round(everything["small_share"], 4),
        zero_area=everything["zero_area"],
        duplicates=everything["duplicates"],
        unsnapped_vertices=everything["unsnapped_vertices"],
        opaque={
            k: round(topology[k], 4)
            for k in ("border_m", "open_m", "tjunction_m", "nearmiss_m", "nonmanifold_m")
        }
        | {"flipped_edges": topology["flipped_edges"], "triangles": topology["triangles"]},
        solids={
            "count": len(solids),
            "closed": len(closed),
            "closed_area_share": round(sum(s["area_m2"] for s in closed) / area, 4),
            "inside_out": sum(s["inside_out"] for s in closed),
            "thinnest_m": round(min(thickness), 4) if thickness else None,
            "thin": sum(t < measure.THIN / 2 for t in thickness),
            "volume_m3": round(sum(abs(s["volume_m3"]) for s in closed), 3),
        },
        repeats=measure.repeats(solids),
    )
    metric = [measure.uv_stretch(p) for p in primitives if p.material.params.get("uv") != "atlas"]
    with_uv = sum(m[0] for m in metric)
    report["uv"] = {
        "missing_share": round(
            1
            - sum(
                measure.areas(p.positions, p.triangles)[0].sum()
                for p in primitives
                if p.uvs is not None
            )
            / max(everything["area_m2"], 1e-9),
            4,
        ),
        "stretched_share": round(sum(m[1] for m in metric) / with_uv, 4) if with_uv else 0.0,
    }
    height_field = kind in HEIGHT_FIELDS
    if height_field:
        report["ground"] = {
            k: round(v, 4) for k, v in field.plan_layers(positions, triangles).items()
        }
        if report["ground"]["underside_share"] > 0.001:
            flags.append(
                f"F3 not a height field: {report['ground']['underside_share']:.1%} of the surface faces down"
            )
        if report["ground"]["stacked_share"] > 0.02:
            flags.append(
                f"F4 stacked layers over {report['ground']['stacked_share']:.1%} of the plan"
            )
    elif len(solid_triangles):
        report["thickness"] = measure.ray_thickness(solid_positions, solid_triangles, samples=rays)
        report["field"] = field.enclosure(solid_positions, solid_triangles)
        state, escape = report["field"]["state"], report["thickness"].get("escape_share", 0.0)
        # Closed, but thinner than the global field's voxels everywhere: lit per instance, not through the field (F5).
        thin = bool(thickness) and max(thickness) < measure.THIN and len(closed) == len(solids)
        report["field"]["thin_object"] = thin
        opaque = report["opaque"]
        if opaque["open_m"] > 0.05:
            flags.append(
                f"G2 open borders {opaque['open_m']:.1f} m ({len(solids) - len(closed)} of {len(solids)} pieces not closed)"
            )
        if report["solids"]["inside_out"]:
            flags.append(f"G2 {report['solids']['inside_out']} solids wound inside out")
        if opaque["flipped_edges"] > 0:
            flags.append(f"G2 {opaque['flipped_edges']} edges with inconsistent winding")
        if not thin and (state != "sealed" or escape > 0.02):
            rule = "F1" if kind == "building" else "G2"
            sealed = report["field"]["sealed_voxel_m"]
            where = (
                "has no inside"
                if sealed is None
                else f"is sealed only at {sealed * 100:.0f} cm voxels"
                if state == "leaks"
                else "is sealed"
            )
            flags.append(
                f"{rule} distance field at {report['field']['voxel_m'] * 100:.0f} cm {where}; "
                f"{escape:.0%} of directions from inside the surface escape"
            )
        if report["materials"]["two_sided_opaque_m2"] > 0.01:
            flags.append(
                f"F5 double-sided opaque surface {report['materials']['two_sided_opaque_m2']:.2f} m2 (model the thickness)"
            )
    if (topology["tjunction_m"] > 0.02 or topology["nearmiss_m"] > 0.02) and not height_field:
        flags.append(
            f"G3 T-junctions {topology['tjunction_m']:.1f} m, near misses {topology['nearmiss_m']:.1f} m"
        )
    elif height_field and (everything["tjunction_m"] > 0.02):
        flags.append(f"G3 T-junctions {everything['tjunction_m']:.1f} m inside the cell")
    if everything["zero_area"] or everything["duplicates"]:
        flags.append(
            f"G3 {everything['zero_area']} zero-area and {everything['duplicates']} duplicate triangles"
        )
    if everything["small_share"] > 0.2 and everything["triangles"] > 200:
        flags.append(f"G4 {everything['small_share']:.0%} of triangles under 0.25 cm2")
    report["repeats"]["repeated_share"] = round(measure.repeated_share(positions, triangles), 4)
    if (
        everything["triangles"] >= 2000
        and report["repeats"]["repeated_share"] > 0.5
        and not height_field
    ):
        flags.append(
            f"I1 {report['repeats']['repeated_share']:.0%} of the triangles are repeated shapes baked into the mesh "
            f"({report['repeats']['copies']} loose copies of {report['repeats']['shapes']} solids)"
        )
    materials = report["materials"]
    if materials["unknown_families"]:
        flags.append(
            "M1 families not in families.json: " + ", ".join(materials["unknown_families"])
        )
    if materials["unknown_params"] or materials["nested_params"]:
        flags.append(
            "M1 parameters undeclared or nested: "
            + ", ".join(sorted({*materials["unknown_params"], *materials["nested_params"]})[:6])
        )
    if materials["placeholder_fallback"]:
        flags.append(
            f"M2 {materials['placeholder_fallback']} materials keep the placeholder fallback colour"
        )
    share = materials["surface_share"]
    if share.get("masked", 0) > 0.05 and kind not in FOLIAGE and kind != "asset:foliage":
        flags.append(f"M3 masked surface {share['masked']:.0%}")
    if report["uv"]["stretched_share"] > 0.05:
        flags.append(
            f"M4 UVs off metric scale on {report['uv']['stretched_share']:.0%} of the surface"
        )
    report["flags"] = flags
    return report, (points.astype(np.float32), welded.astype(np.uint32))


def hierarchy_flags(report: dict) -> None:
    result = report.get("hierarchy") or {}
    if "needed" in result and report["triangles"] >= 1000 and result["needed"]["300m"] > 0.25:
        report["flags"].append(
            f"G2/G3 cluster hierarchy stalls: {result['needed']['300m']:.0%} of the triangles are still needed "
            f"at 300 m with borders locked ({result['neededFree']['300m']:.0%} if cracks are accepted)"
        )


def resolve(store: Path, url: str) -> Path:
    if url.startswith("/assets/"):
        return REPO / "game/public" / url.lstrip("/")
    return store / url


def border_mismatch(cells: dict[str, tuple[np.ndarray, np.ndarray]]) -> list[dict]:
    """Ground cells whose shared border does not carry the same vertices on both sides (F3). `cells` maps an id to
    (absolute positions, bounds [min, max])."""
    out = []
    names = sorted(cells)
    for first, a in enumerate(names):
        for b in names[first + 1 :]:
            (pa, (alo, ahi)), (pb, (blo, bhi)) = cells[a], cells[b]
            for axis in (0, 2):
                other = 2 - axis
                for plane, hit in (
                    (ahi[axis], abs(ahi[axis] - blo[axis]) < 0.02),
                    (alo[axis], abs(alo[axis] - bhi[axis]) < 0.02),
                ):
                    overlap = min(ahi[other], bhi[other]) - max(alo[other], blo[other])
                    if not hit or overlap < 1.0:
                        continue
                    side_a = pa[np.abs(pa[:, axis] - plane) < 2e-3]
                    side_b = pb[np.abs(pb[:, axis] - plane) < 2e-3]
                    key_a = {
                        tuple(row)
                        for row in np.round(side_a[:, [other, 1]] / 1e-3).astype(np.int64)
                    }
                    key_b = {
                        tuple(row)
                        for row in np.round(side_b[:, [other, 1]] / 1e-3).astype(np.int64)
                    }
                    if key_a != key_b:
                        out.append(
                            {
                                "cells": [a, b],
                                "vertices": [len(key_a), len(key_b)],
                                "unmatched": len(key_a ^ key_b),
                            }
                        )
    return out


def entity_job(args: tuple) -> tuple[str, dict, tuple | None, np.ndarray | None]:
    """One entity's mesh (run in a worker): (id, report, welded mesh, absolute positions of a ground cell)."""
    identity, kind, path, origin, rays = args
    primitives = read(path)
    report, mesh = check_mesh(primitives, kind, rays=rays)
    absolute = None
    if kind in HEIGHT_FIELDS and primitives:
        absolute = np.vstack([p.positions for p in primitives]) + np.asarray(origin)
    return identity, report, mesh, absolute
