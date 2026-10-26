"""Join surveys, measured lidar, and exact ground contacts into structures."""

from __future__ import annotations

from collections import Counter

import numpy as np
from shapely.geometry import LineString
from shapely.ops import unary_union

from .geometry import profile_wall, quay, stairs
from .survey import group_flights, sample_line


def build(records, lidar, ground):
    meshes, issues, measurements = [], [], []
    lines = [
        (r["object_id"], r["geometry"], r["code"])
        for r in records
        if r["code"] in ("66626", "66618")
    ]
    flights, rejected = group_flights(lines)
    issues.extend(rejected)

    def flight(object_id, a, b, width, sources):
        ah, ae = lidar.landing(a)
        bh, be = lidar.landing(b)
        if ah > bh:
            a, b, ah, bh, ae, be = b, a, bh, ah, be, ae
        if bh - ah < 0.05:
            raise ValueError("stair rise under 5 cm: survey/lidar disagree")
        ga, gb = ground.height(a, reference=ah), ground.height(b, reference=bh)
        errors = [abs(ga - ah) + ae, abs(gb - bh) + be]
        contacts = [0.0]
        side = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
        # Check the whole landing edge: a flat-width generator must not conceal crossfall.
        for centre, h in ((a, ga), (b, gb)):
            for sign in (-1, 1):
                xz = centre + side * sign * width / 2
                lh, le = lidar.landing(xz)
                gh = ground.height(xz, reference=h)
                errors.append(abs(h - lh) + le)
                contacts.append(abs(h - gh))
        if max(contacts) > 0.02:
            raise ValueError(f"stair landing contact gap {max(contacts):.3f} m exceeds 0.02 m")
        if max(errors) > 0.05:
            raise ValueError(f"stair landing/ground mismatch {max(errors):.3f} m exceeds 0.05 m")
        generated, meta = stairs(object_id, [a[0], ga, a[1]], [b[0], gb, b[1]], width)
        meshes.extend(generated)
        measurements.append(
            {
                "object_id": object_id,
                "sources": sources,
                **meta,
                "landing_error_bound_m": max(errors),
                "ground_contact_error_m": max(contacts),
                "visual_collider_endpoint_error_m": 0.0,
            }
        )
        if meta["flagged_steep"]:
            issues.append(
                {
                    "object_id": object_id,
                    "reason": f"stair slope {meta['slope_deg']:.2f} exceeds 40 degrees",
                }
            )

    for item in flights:
        try:
            flight(item.object_id, item.bottom_xz, item.top_xz, item.width, item.source_ids)
        except ValueError as e:
            issues.append(
                {"object_id": item.object_id, "reason": str(e), "sources": item.source_ids}
            )
    for record in records:
        code, object_id, line = record["code"], record["object_id"], record["geometry"]
        try:
            if code == "steps":
                width = float(record["tags"].get("width", 2))
                p = np.asarray(line.coords)[:, :2]
                for k, (a, b) in enumerate(zip(p[:-1], p[1:], strict=True)):
                    flight(f"{object_id}:{k}", a, b, width, [object_id])
                continue
            if code not in (
                "66634",
                "wall",
                "retaining_wall",
                "66630",
                "66632",
                "66636",
                "fence",
                "railing",
            ):
                continue
            xz = sample_line(line)
            if code in ("66634", "wall", "retaining_wall"):
                tangent = np.gradient(xz, axis=0)
                side = np.column_stack((-tangent[:, 1], tangent[:, 0]))
                side /= np.linalg.norm(side, axis=1)[:, None]
                if code in ("66634", "retaining_wall"):
                    # The surveyed line is a wall top, separating two measured
                    # terrain surfaces. Sample beyond the wall face on each side.
                    sides = np.array(
                        [
                            [ground.height(p - v * 0.6), ground.height(p + v * 0.6)]
                            for p, v in zip(xz, side, strict=True)
                        ]
                    )
                    low, high = sides.min(axis=1), sides.max(axis=1)
                    evidence = {"source": "ground_sides", "offset_m": 0.6}
                else:
                    low = np.array([ground.height(p) for p in xz])
                    tagged_height = record.get("tags", {}).get("height")
                    if tagged_height is not None:
                        height = float(tagged_height)
                        if not 0.3 <= height <= 12:
                            raise ValueError("invalid surveyed wall height")
                        high = low + height
                        evidence = {"source": "osm_height", "height_m": height}
                    else:
                        samples = [lidar.wall_top(p) for p in xz]
                        high = np.array([s[0] for s in samples])
                        evidence = {
                            "source": "lidar_class1_top",
                            "lidar_height_error_m": max(s[1] for s in samples),
                        }
                meshes.append(profile_wall(object_id, xz, low, high))
                measurements.append(
                    {
                        "object_id": object_id,
                        "kind": "wall",
                        "height_evidence": evidence,
                        "profile_xz": xz.tolist(),
                        "lower_y": low.tolist(),
                        "upper_y": high.tolist(),
                        "max_height_m": float((high - low).max()),
                    }
                )
            else:
                low = np.array([ground.height(p) for p in xz])
                samples = [lidar.fence_height(p, h) for p, h in zip(xz, low, strict=True)]
                high = low + [s[0] for s in samples]
                material = "railing" if code == "railing" else "fence"
                meshes.append(
                    profile_wall(object_id, xz, low, high, thickness=0.08, material=material)
                )
                measurements.append(
                    {
                        "object_id": object_id,
                        "kind": material,
                        "height_evidence": dict(Counter(s[1] for s in samples)),
                        "profile_xz": xz.tolist(),
                        "lower_y": low.tolist(),
                        "upper_y": high.tolist(),
                    }
                )
        except ValueError as e:
            issues.append({"object_id": object_id, "reason": str(e)})
    for object_id, xz, lower, upper in ground.quays:
        try:
            meshes.extend(quay(object_id, xz, lower, upper))
        except ValueError as e:
            issues.append({"object_id": object_id, "reason": str(e)})
            continue
        measurements.append(
            {
                "object_id": object_id,
                "kind": "quay",
                "source": "ground classification",
                "profile_xz": xz.tolist(),
                "lower_y": lower.tolist(),
                "upper_y": upper.tolist(),
                "length_m": float(LineString(xz).length),
                "ground_contact_error_m": 0.0,
            }
        )
    # Ground owns ramp collision and visuals; its measured handoff names coverage.
    ramp_area = unary_union(ground.ramps).buffer(0.05) if ground.ramps else None
    for record in records:
        if record["code"] == "62540" and (
            ramp_area is None or not ramp_area.covers(record["geometry"])
        ):
            issues.append(
                {
                    "object_id": record["object_id"],
                    "reason": "ramp survey is not covered by measured ground ramp terrain",
                }
            )
    if not meshes:
        issues.append({"object_id": "layer", "reason": "no structures could be reconstructed"})
    metrics = {
        "status": "BLOCKED" if issues else "READY_FOR_VALIDATION",
        "input_counts": dict(Counter(r["code"] for r in records)),
        "object_counts": dict(Counter(m.get("kind", "unknown") for m in measurements)),
        "measurements": measurements,
        "issues": issues,
        "acceptance": "Real-data KCC/car traversal remains required after assembly.",
    }
    return meshes, metrics
