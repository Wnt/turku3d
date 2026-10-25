"""Ground-layer build entry point. Never writes shipped .col/tileset artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id, table
from turku3d.package.gltf import GlbMesh, write_glb

from .breaklines import load_breaklines, records, validation_geojson
from .curbs import Curb, fit_curbs
from .inputs import fingerprints, read_lidar, source_paths, vectors
from .materials import Materials
from .mesh import Mesh, build_mesh, parts, tile_mesh
from .points import Surface, select_ground, split_points
from .qa import evaluate, write_report


def log(message):
    print(message, flush=True)


def code_hash():
    files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path(C.__file__),
        Path(__file__).parents[1] / "package/gltf.py",
        Path(__file__).parents[1] / "col/materials_v1.json",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def visual_meshes(mesh, tile, curbs):
    out = []
    selected = tile_mesh(mesh, tile)
    for material in np.unique(selected.material):
        # Raised curb collision strips are replaced by the visual bevel below.
        if material == mat_id("curb"):
            continue
        part = Mesh(
            selected.positions,
            selected.indices[selected.material == material],
            selected.material[selected.material == material],
        ).compact()
        name = table()[int(material)].name
        object_id = f"ground:{tile.id}:{name}"
        out.append(
            GlbMesh(
                object_id,
                part.positions,
                part.indices,
                uvs=part.positions[:, [0, 2]] * [1, -1],
                extras={
                    "layer": "ground",
                    "object_id": object_id,
                    "material_key": name,
                    "sensor": name == "water",
                },
            )
        )
    envelope = shapely.box(*tile.bbox.as_tuple())
    for curb in curbs:
        if curb.kind != "curb":
            continue
        left, right = curb.lines()
        positions, faces = [], []
        for i in range(len(left) - 1):
            cross_sections = []
            low_side, high_side = (
                (left, right)
                if left[i : i + 2, 2].mean() < right[i : i + 2, 2].mean()
                else (right, left)
            )
            for j in (i, i + 1):
                low, high = low_side[j], high_side[j]
                bevel = high.copy()
                bevel[:2] = high[:2] * 0.75 + low[:2] * 0.25
                bevel[2] -= max(0.0, min(0.015, (high[2] - low[2]) / 3))
                cross_sections.append(np.array([low, bevel, high]))
            a, b = cross_sections
            for k in (0, 1):
                polygon = shapely.Polygon([a[k], b[k], b[k + 1], a[k + 1]])
                if not polygon.is_valid or polygon.area < 1e-12:
                    continue
                for part in parts(polygon.intersection(envelope), ("Polygon",)):
                    xyz = np.asarray(part.exterior.coords)[:-1][:, [0, 2, 1]]
                    start = len(positions)
                    positions.extend(xyz)
                    for v in range(1, len(xyz) - 1):
                        face = [start, start + v, start + v + 1]
                        tri = np.array([positions[q] for q in face])
                        if np.cross(tri[1] - tri[0], tri[2] - tri[0])[1] < 0:
                            face.reverse()
                        faces.append(face)
        if faces:
            p = np.asarray(positions)
            object_id = f"curb:{curb.object_id}"
            out.append(
                GlbMesh(
                    object_id,
                    p,
                    np.asarray(faces, np.uint32),
                    uvs=p[:, [0, 2]] * [1, -1],
                    extras={
                        "layer": "ground",
                        "object_id": object_id,
                        "material_key": "curb",
                        "visual_only_bevel": True,
                    },
                )
            )
    return out


def run(args):
    started = time.monotonic()
    visual = [C.VisualTile.parse(t) for t in args.tiles]
    if any(t.level != 0 for t in visual):
        raise ValueError("Ground builds accept only L0 tiles")
    tiles = sorted({t for v in visual for t in v.collision_tiles()})
    core = C.BBoxLocal(
        min(t.bbox.xmin for t in tiles),
        min(t.bbox.zmin for t in tiles),
        max(t.bbox.xmax for t in tiles),
        max(t.bbox.zmax for t in tiles),
    )
    bounds = core.buffered(C.MESH_MARGIN)
    output = C.work_root() / "build/layers/ground"
    reports = C.work_root() / "reports/ground"
    paths = source_paths(bounds, args.source_area)
    # Track the VRT's referenced TIFFs as well as its XML (pixel changes invalidate tiles).
    import xml.etree.ElementTree as ET

    for i, node in enumerate(ET.parse(paths["ortho"]).findall(".//SourceFilename")):
        path = Path(node.text)
        if not path.is_absolute():
            path = paths["ortho"].parent / path
        paths[f"ortho_chunk_{i}"] = path.resolve()
    if getattr(args, "breaklines", None):
        paths["breaklines"] = args.breaklines.resolve()
    inputs = fingerprints(paths)
    params = {
        "visual_tiles": [v.id for v in visual],
        "margin": C.MESH_MARGIN,
        "adaptive_tolerance": 0.01,
        "station_spacing": 1.0,
        "breakline_half_width": 0.03,
        "water_level_override": args.water_level,
    }
    identity = {"inputs": inputs, "params": params, "code_version": code_hash()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    manifest_path = output / "MANIFEST.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if (
            previous.get("cache_key") == key
            and (reports / "ground-qa.json").exists()
            and previous.get("qa_sha256")
            == hashlib.sha256((reports / "ground-qa.json").read_bytes()).hexdigest()
            and all(
                (output / f).exists()
                and hashlib.sha256((output / f).read_bytes()).hexdigest() == digest
                for f, digest in previous["artifacts"].items()
            )
        ):
            report = json.loads((reports / "ground-qa.json").read_text())
            log(
                json.dumps(
                    {
                        "cached": True,
                        "collision_tiles": len(tiles),
                        "seconds": round(time.monotonic() - started, 3),
                        "acceptance_passed": report["passed"],
                    }
                )
            )
            return 0 if report["passed"] else 2
    cache = C.cache_dir("ground")
    raw_key = hashlib.sha256(
        json.dumps(
            {k: v for k, v in inputs.items() if v["path"].endswith(".laz")}, sort_keys=True
        ).encode()
        + repr(bounds).encode()
    ).hexdigest()
    raw_cache = cache / f"lidar-{raw_key}.npz"
    if raw_cache.exists():
        with np.load(raw_cache) as data:
            points, classes, water_heights = data["points"], data["classes"], data["water_heights"]
    else:
        log("Reading class-2/12 lidar and class-9 water returns (read-only raw inputs)")
        points, classes, water_heights = read_lidar(paths, bounds)
        temporary = raw_cache.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez(stream, points=points, classes=classes, water_heights=water_heights)
        temporary.replace(raw_cache)
    log(f"Loaded {len(points):,} class-2/12 points; {len(water_heights):,} class-9 returns")
    lines, water, buildings, crossings, streets = vectors(paths, bounds)
    (training, train_cls), (heldout, held_cls) = split_points(points, classes)
    selection_key = hashlib.sha256(
        (raw_key + Path(__file__).with_name("points.py").read_text() + water.wkb_hex).encode()
    ).hexdigest()
    selection_cache = cache / f"selection-{selection_key}.npz"
    if selection_cache.exists():
        with np.load(selection_cache, allow_pickle=False) as data:
            accepted, heldout = data["accepted"], data["heldout"]
            selection = json.loads(str(data["stats"]))
    else:
        log("Selecting and denoising ground points")
        accepted, selection = select_ground(training, train_cls)
        accepted = accepted[~shapely.contains_xy(water, accepted[:, 0], accepted[:, 2])]
        # Held-out class 2 is never residual-trimmed. Class 12 eligibility uses TRAINING class 2.
        reference = Surface(training[train_cls == 2])
        h, _ = reference.sample(heldout[:, [0, 2]])
        heldout = heldout[
            (held_cls == 2) | ((held_cls == 12) & (np.abs(heldout[:, 1] - h) <= 0.15))
        ]
        np.savez(selection_cache, accepted=accepted, heldout=heldout, stats=json.dumps(selection))
    surface = Surface(accepted)
    if args.water_level is not None:
        water_level, water_source = args.water_level, "explicit N2000 level override"
    elif water.is_empty:
        water_level, water_source = 0.0, "no water polygons in requested envelope"
    elif len(water_heights) >= 20:
        water_level, water_source = float(np.median(water_heights)), "median class-9 lidar"
    else:
        wet_points = training[shapely.contains_xy(water.buffer(-3), training[:, 0], training[:, 2])]
        if len(wet_points) < 20:
            raise ValueError(
                "No reliable water returns: provide --water-level N2000_METRES; no implicit sea-level guess"
            )
        # Water returns can be labelled overlap/ground in this source. Lowest modal 5 cm
        # height bin excludes bridge decks; require adequate support and report provenance.
        bins, counts = np.unique(np.rint(wet_points[:, 1] / 0.05).astype(int), return_counts=True)
        significant = counts >= max(20, counts.max() * 0.1)
        level_bin = bins[significant].min()
        sample = wet_points[np.abs(wet_points[:, 1] - level_bin * 0.05) <= 0.1, 1]
        water_level, water_source = (
            float(np.median(sample)),
            f"lowest supported river-interior lidar mode ({len(sample)} returns)",
        )
    log(f"Ground selection: {selection}; water level {water_level:.3f} ({water_source})")
    curb_key = hashlib.sha256(
        (
            selection_key
            + Path(__file__).with_name("curbs.py").read_text()
            + repr([(k, g.wkb_hex) for k, g in lines])
            + crossings.wkb_hex
            + str(water_level)
        ).encode()
    ).hexdigest()
    curb_cache = cache / f"curbs-{curb_key}.json"
    if curb_cache.exists():
        saved = json.loads(curb_cache.read_text())
        curbs = [
            Curb(
                **{
                    k: np.array(v)
                    if k
                    in ("distances", "fitting_distances", "centers", "normals", "heights", "shifts")
                    else v
                    for k, v in c.items()
                }
            )
            for c in saved["curbs"]
        ]
        rejected = saved["rejected"]
    else:
        log(f"Fitting {len(lines)} city curb/sidewalk-edge lines")
        curbs, rejected = fit_curbs(lines, surface, crossings, water, water_level)
        saved = {
            "curbs": [
                {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in vars(c).items()}
                for c in curbs
            ],
            "rejected": rejected,
        }
        curb_cache.write_text(json.dumps(saved))

    if getattr(args, "breaklines", None):
        supplied = load_breaklines(args.breaklines)
        replacement_ids = {c.object_id for c in supplied}
        curbs = [c for c in curbs if c.object_id not in replacement_ids] + supplied

    log(f"Fitted {len(curbs)} curb/edge lines; {len(rejected)} lack fitting support")
    materials = Materials(streets, paths["ortho"])
    mesh_key = hashlib.sha256(
        (
            selection_key
            + curb_key
            + str(water_level)
            + repr(params)
            + Path(__file__).with_name("mesh.py").read_text()
            + Path(__file__).with_name("materials.py").read_text()
            + json.dumps(inputs, sort_keys=True)
        ).encode()
    ).hexdigest()
    mesh_cache = cache / f"mesh-{mesh_key}.npz"
    info_cache = cache / f"mesh-{mesh_key}.json"
    if mesh_cache.exists() and info_cache.exists():
        mesh = Mesh.load(mesh_cache)
        mesh_info = json.loads(info_cache.read_text())
        log("Using cached canonical CDT")
    else:
        log("Building canonical adaptive CDT")
        mesh, mesh_info = build_mesh(surface, bounds, tiles, curbs, water, water_level, materials)
        temporary = mesh_cache.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez(stream, positions=mesh.positions, indices=mesh.indices, material=mesh.material)
        temporary.replace(mesh_cache)
        info_cache.write_text(json.dumps(mesh_info))
    log(f"CDT: {mesh_info}")
    meshes = {tile: tile_mesh(mesh, tile) for tile in tiles}
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for tile, part in meshes.items():
        name = f"{tile.id}.col.npz"
        part.save(output / name)
        artifacts.append(name)
    for tile in visual:
        name = f"{tile.id}.vis.glb"
        write_glb(
            visual_meshes(mesh, tile, curbs),
            output / name,
            root_extras={"layer": "ground", "tile": tile.id},
        )
        artifacts.append(name)
    # Quays are a handoff, never disguised as a curb collider.
    handoff = records([c for c in curbs if c.kind == "quay"])
    for name, data in [
        ("quays.json", handoff),
        ("breaklines.json", records(curbs)),
        ("curbs.geojson", validation_geojson(curbs)),
    ]:
        (output / name).write_text(json.dumps(data, indent=2) + "\n")
        artifacts.append(name)
    log("Evaluating withheld lidar, curb stations and seams")
    report = evaluate(
        meshes, accepted, heldout, curbs, water, buildings, core, materials, surface, lines
    )
    report.update(
        {
            "selection": selection,
            "mesh_build": mesh_info,
            "rejected_curb_lines": rejected,
            "source_lines": len(lines),
            "short_lines_without_stations": len(lines) - len(curbs) - len(rejected),
            "quay_lines": len(handoff),
            "water_level": {"metres_n2000": water_level, "source": water_source},
            "build_seconds": time.monotonic() - started,
            "cache_key": key,
        }
    )
    write_report(report, reports)
    manifest = {
        **identity,
        "layer": "ground",
        "validation": {"curbs": "curbs.geojson"},
        "schema_version": 1,
        "cache_key": key,
        "tiles_written": [t.id for t in tiles] + [t.id for t in visual],
        "artifacts": {f: hashlib.sha256((output / f).read_bytes()).hexdigest() for f in artifacts},
        "collision_tile_cache": {
            t.id: {
                "key": hashlib.sha256((key + t.id).encode()).hexdigest(),
                "artifact": f"{t.id}.col.npz",
            }
            for t in tiles
        },
        "qa": str(reports / "ground-qa.json"),
        "qa_sha256": hashlib.sha256((reports / "ground-qa.json").read_bytes()).hexdigest(),
        "acceptance_passed": report["passed"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    log(
        json.dumps(
            {
                "checks": report["checks"],
                "seconds": round(time.monotonic() - started, 2),
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0 if report["passed"] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", default=["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"])
    parser.add_argument(
        "--breaklines", type=Path, help="Measured breakline JSON; matching IDs replace city fits"
    )
    parser.add_argument("--source-area", default="work-3x3")
    parser.add_argument(
        "--water-level",
        type=float,
        help="Explicit N2000 water elevation when no reliable lidar returns exist",
    )
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
