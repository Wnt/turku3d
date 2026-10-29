"""Read-only river-walk roughness and shipped collision coverage measurements."""

import json
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id

from . import banks, surfaces, walkable
from .build import grid_mesh
from .inputs import gather
from .sources import Sources


def heights(triangles, query, target):
    """Independent vertical ray/triangle test; choose the floor nearest the surveyed walk."""
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    triangles = triangles[np.abs(normal[:, 1]) > 1e-8]
    tree = shapely.STRtree(shapely.polygons(triangles[:, :, [0, 2]]))
    q, t = tree.query(shapely.points(query), predicate="intersects")
    triangle = triangles[t]
    a = triangle[:, 0]
    b, c = triangle[:, 1] - a, triangle[:, 2] - a
    d = query[q] - a[:, [0, 2]]
    det = b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0]
    u = (d[:, 0] * c[:, 2] - d[:, 1] * c[:, 0]) / det
    v = (b[:, 0] * d[:, 1] - b[:, 2] * d[:, 0]) / det
    hit = a[:, 1] + u * b[:, 1] + v * c[:, 1]
    # Sort by error, then take the first hit per ray. Water sensors/bed cannot
    # hide a missing dry walking surface merely by catching the ray far below it.
    order = np.argsort(abs(hit - target[q]), kind="stable")
    _, first = np.unique(q[order], return_index=True)
    result = np.full(len(query), np.nan)
    selected = order[first]
    result[q[selected]] = hit[selected]
    return result


def roughness(y):
    finite = np.isfinite(y)
    pairs = finite[1:] & finite[:-1]
    dents = finite[4:-4] & finite[:-8] & finite[8:]
    return dict(
        samples=len(y),
        missing=int((~finite).sum()),
        max_step_25cm_m=float(np.max(abs(np.diff(y))[pairs], initial=0)),
        max_dent_2m_m=float(np.max(abs(y[4:-4] - (y[:-8] + y[8:]) / 2)[dents], initial=0)),
    )


def audit(root, output):
    from shapely.ops import substring

    manifest = json.loads((output / "manifest.json").read_text())
    tile = C.VisualTile.parse(manifest["tile"])
    inputs = gather(tile, root, Sources())
    window = shapely.box(*tile.bbox.as_tuple()).buffer(-0.02)
    grid, idx = grid_mesh(tile, inputs.heights)
    centres = grid[idx].mean(axis=1)
    ids = surfaces.classify(centres, inputs.ortho, tile, inputs.streets, inputs.surfaces)
    before, *_ = banks.produce(
        grid,
        idx,
        ids,
        np.ones(len(idx), bool),
        inputs.water,
        manifest["water_level"],
        inputs.surfaces,
        inputs.barriers,
    )
    collision = [
        read_col((output / entry["url"]).read_bytes()) for entry in manifest["collision"].values()
    ]
    triangles = np.concatenate(
        [col.world_positions()[col.indices[col.materials != mat_id("water")]] for col in collision]
    )
    excluded = shapely.union_all(
        [
            *[line.buffer(4) for _, line, _ in inputs.stairs[0]],
            *[b.footprint.buffer(0.5) for b in [*inputs.buildings, *inputs.wall_context]],
            *[
                w["line"].buffer(w["width"] / 2 + 3)
                for w in (inputs.roads or {}).get("bridge_ways", [])
            ],
        ]
    )
    rows, total_missing, domains = [], 0, []
    for patch in inputs.walkable:
        line = patch["line"]
        station = np.arange(2, line.length - 2, 0.25)
        query = shapely.get_coordinates(shapely.line_interpolate_point(line, station))
        if not len(query):
            continue
        # A sentinel distinguishes unsupported knots from measured core heights.
        expected = walkable.grade(
            np.c_[query[:, 0], np.full(len(query), -999.0), query[:, 1]], [patch]
        )[:, 1]
        selected = shapely.contains_xy(window, query[:, 0], query[:, 1])
        selected &= ~shapely.intersects_xy(excluded, query[:, 0], query[:, 1])
        selected &= np.isfinite(expected) & (expected > -900)
        selected &= shapely.distance(shapely.points(query), shapely.union_all(inputs.water)) < 25
        if selected.sum() < 9:
            continue
        supported = np.isfinite(patch["heights"][:, 3])
        for i in np.flatnonzero(supported[:-1] & supported[1:]):
            segment = substring(line, patch["knots"][i], patch["knots"][i + 1])
            domains.append(segment.buffer(min(1, patch["width"] / 2 - 0.2), cap_style="flat"))
        old = heights(before.positions[before.indices], query[selected], expected[selected])
        new = heights(triangles, query[selected], expected[selected])
        missing = ~np.isfinite(new) | (abs(new - expected[selected]) > 0.35)
        total_missing += int(missing.sum())
        # Keep missing/excluded intervals out of step and dent measurements.
        full_old, full_new = np.full(len(query), np.nan), np.full(len(query), np.nan)
        full_old[selected], full_new[selected] = old, new
        row = dict(
            id=patch["key"],
            before=roughness(full_old),
            after=roughness(full_new),
            collision_misses=int(missing.sum()),
            surface_error_p90_m=float(np.nanquantile(abs(new - expected[selected]), 0.9)),
        )
        # Reproduce the supplied recording's long, unbroken lower-quay reach.
        if patch["key"] == "769441193":
            focus = selected & (station >= 45) & (station <= 225)
            row["recorded_reach"] = dict(
                before=roughness(np.where(focus, full_old, np.nan)),
                after=roughness(np.where(focus, full_new, np.nan)),
            )
        rows.append(row)
    profiles = [
        (key, line, tags["_profile"])
        for key, line, tags in inputs.stairs[0]
        if "_profile" in tags and line.intersects(window)
    ]
    corrections = []
    for key, line, profile in profiles:
        ends = np.asarray(line.coords)[[0, -1]]
        previous = [surfaces.grid_height(tile, inputs.heights, *point) for point in ends]
        corrections.append(
            dict(
                id=key,
                max_endpoint_correction_m=float(
                    np.max(abs(np.asarray(previous) - np.asarray(profile["heights"])[[0, -1]]))
                ),
            )
        )
    domain = shapely.union_all(domains).intersection(window).difference(excluded)
    water = shapely.union_all(inputs.water)
    domain = domain.difference(water).intersection(water.buffer(25))
    centres = triangles.mean(axis=1)
    expected = walkable.grade(
        np.c_[centres[:, 0], np.full(len(centres), -999.0), centres[:, 2]], inputs.walkable
    )[:, 1]
    supported = (expected > -900) & (abs(centres[:, 1] - expected) < 0.35)
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    supported &= np.abs(normal[:, 1]) > 1e-8
    coverage = shapely.union_all(shapely.polygons(triangles[supported][:, :, [0, 2]]))
    holes = [g for g in shapely.get_parts(domain.difference(coverage)) if g.area > 1e-4]
    result = dict(
        tile=tile.id,
        paths=rows,
        collision_ray_misses=total_missing,
        collision_holes=len(holes),
        collision_hole_area_m2=float(sum(g.area for g in holes)),
        audited_area_m2=float(domain.area),
        stairs=manifest["stairs"],
        endpoint_corrections=corrections,
        walkable=manifest["walkable"],
        stage_seconds=manifest.get("seconds", {}),
    )
    (output / "walkable-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    assert rows, f"{tile.id}: no measured river paths"
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.output)), flush=True)
