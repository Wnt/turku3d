"""Acceptance metrics from withheld returns/stations and actual emitted mesh geometry."""

from __future__ import annotations

import json

import numpy as np
import shapely

from turku3d.col.materials import mat_id

from .curbs import Curb, fit_profile, stations
from .mesh import HeightQuery, Mesh, seam_errors
from .points import Surface


def ratio(values):
    return float(np.mean(values)) if len(values) else None


def measure_mesh_step(offsets, profile):
    """Measure the actual transition, allowing lidar/mesh edge registration differences.

    A slope fit must not include the curb face. Locate its peak gradient first, then
    fit the two sides outside a 12 cm guard band. This is independent of the input
    breakline spacing and fails a ramp or absent step rather than declaring it sharp.
    """
    mid = (offsets[:-1] + offsets[1:]) / 2
    gradient = np.diff(profile) / np.diff(offsets)
    slope = np.median(gradient[np.abs(mid) > 0.6])
    search = np.flatnonzero(np.abs(mid) <= 0.45)
    edge = mid[search[np.argmax(np.abs(gradient[search] - slope))]]
    relative = offsets - edge
    masks = [(relative < -0.12) & (relative > -0.8), (relative > 0.12) & (relative < 0.8)]
    coeff = [np.polyfit(relative[m], profile[m], 1) for m in masks]
    step = float(coeff[1][1] - coeff[0][1])
    if abs(step) < 0.01:
        return step, 1.0, float(edge)
    window = np.abs(relative) <= 0.6
    detrended = profile[window] - relative[window] * np.mean([c[0] for c in coeff])
    normalized = (detrended - coeff[0][1]) / step
    rising = np.maximum.accumulate(np.clip(normalized, 0, 1))
    width = float(np.interp(0.9, rising, offsets[window]) - np.interp(0.1, rising, offsets[window]))
    return step, width, float(edge)


def evaluate(
    meshes,
    accepted,
    heldout,
    curbs,
    water,
    buildings,
    bounds,
    materials,
    fitting_surface,
    source_lines=(),
):
    curbs = list(curbs)
    fitted_ids = {c.object_id for c in curbs}
    # Missing/unsupported fits are evaluated too, so rejecting a city line cannot
    # make the mesh-presence score improve by removing its validation stations.
    for object_id, line in source_lines:
        if object_id in fitted_ids:
            continue
        station = stations(line)
        if station is None:
            continue
        d, p, n = station
        validation = [
            {
                "distance": float(d[i]),
                "center": p[i].tolist(),
                "normal": n[i].tolist(),
                "length_weight_m": min(2.0, line.length - d[i] + 1.0),
            }
            for i in range(1, len(d), 2)
        ]
        curbs.append(
            Curb(
                object_id,
                d,
                np.empty(0),
                p,
                n,
                np.zeros_like(p),
                np.zeros(len(p)),
                "unmodelled",
                validation,
            )
        )
    solid_positions, solid_faces, offset = [], [], 0
    for mesh in meshes.values():
        solid = Mesh(
            mesh.positions,
            mesh.indices[mesh.material != mat_id("water")],
            mesh.material[mesh.material != mat_id("water")],
        ).compact()
        solid_positions.append(solid.positions)
        solid_faces.append(solid.indices + offset)
        offset += len(solid.positions)
    p = np.concatenate(solid_positions)
    f = np.concatenate(solid_faces)
    # Weld exact seam vertices for the interpolator; collider files remain unchanged.
    p, inv = np.unique(p, axis=0, return_inverse=True)
    query = HeightQuery(Mesh(p, inv[f], np.zeros(len(f), np.uint8)))
    forbidden = shapely.union_all([g for g in (water, buildings.buffer(0.5)) if not g.is_empty])
    x = np.arange(bounds.xmin + 0.5, bounds.xmax, 1.0)
    z = np.arange(bounds.zmin + 0.5, bounds.zmax, 1.0)
    gx, gz = np.meshgrid(x, z)
    cells = np.c_[gx.ravel(), gz.ravel()]
    open_cells = ~shapely.contains_xy(forbidden, *cells.T)
    origin = np.floor([bounds.xmin, bounds.zmin]).astype(np.int64)
    dimensions = np.ceil([bounds.xmax, bounds.zmax]).astype(np.int64) - origin
    point_cells = np.floor(accepted[:, [0, 2]]).astype(np.int64) - origin
    valid_cells = np.all((point_cells >= 0) & (point_cells < dimensions), axis=1)
    point_cells = point_cells[valid_cells]
    keys = np.unique(point_cells[:, 1] * dimensions[0] + point_cells[:, 0])
    query_cells = np.floor(cells).astype(np.int64) - origin
    covered = np.isin(query_cells[:, 1] * dimensions[0] + query_cells[:, 0], keys)
    q = heldout[:, [0, 2]]
    inside = (
        (q[:, 0] >= bounds.xmin)
        & (q[:, 0] < bounds.xmax)
        & (q[:, 1] >= bounds.zmin)
        & (q[:, 1] < bounds.zmax)
    )
    keep = inside & ~shapely.contains_xy(forbidden, *q.T)
    q, truth = q[keep], heldout[keep, 1]
    prediction = query(q)
    finite = np.isfinite(prediction)
    error = prediction[finite] - truth[finite]
    # Noise is measured on untouched withheld returns on pavement, away from breaklines.
    curb_geometry = shapely.union_all([shapely.LineString(c.centers) for c in curbs]).buffer(1.0)
    pavement = np.isin(
        materials.lookup(q), [mat_id(n) for n in ["road", "sidewalk", "paving_slab", "cobblestone"]]
    )
    flat = pavement & ~shapely.contains_xy(curb_geometry, *q.T)
    local_height = fitting_surface.sample(q[flat])[0] if flat.any() else np.empty(0)
    residual = truth[flat] - local_height
    noise_mad = float(np.median(np.abs(residual - np.median(residual)))) if flat.any() else None
    station_results, controls, profiles = [], [], []
    validation_surface = Surface(heldout) if curbs and len(heldout) >= 6 else None
    unsupported_stations = 0
    unsupported_length = 0.0
    offsets = np.linspace(-1.2, 1.2, 241)
    for curb in curbs:
        if curb.kind == "quay":
            continue
        for station in curb.validation:
            center, normal = np.array(station["center"]), np.array(station["normal"])
            if not (
                bounds.xmin + 1 < center[0] < bounds.xmax - 1
                and bounds.zmin + 1 < center[1] < bounds.zmax - 1
            ):
                continue
            # Only withheld lidar returns define validation truth. Generic terrain
            # samples near an odd station therefore cannot leak into its reference.
            observed = (
                fit_profile(validation_surface, center, normal, along_limit=0.75)
                if validation_surface
                else None
            )
            if observed is None:
                unsupported_stations += 1
                unsupported_length += station.get("length_weight_m", 2.0)
                continue
            shift, truth_h = observed
            center = center + shift * normal
            truth_step = truth_h[1] - truth_h[0]
            profile = query(center + offsets[:, None] * normal)
            if not np.all(np.isfinite(profile)):
                station_results.append(
                    {
                        "object_id": curb.object_id,
                        "distance": station["distance"],
                        "center_xz": center.tolist(),
                        "length_weight_m": station.get("length_weight_m", 2.0),
                        "lidar_step": float(truth_step),
                        "error_m": None,
                        "width_m": None,
                    }
                )
                continue
            mesh_step, measured_width, edge_offset = measure_mesh_step(offsets, profile)
            width = measured_width if abs(truth_step) >= 0.06 else None
            station_results.append(
                {
                    "object_id": curb.object_id,
                    "distance": station["distance"],
                    "center_xz": center.tolist(),
                    "length_weight_m": station.get("length_weight_m", 2.0),
                    "lidar_step": float(truth_step),
                    "mesh_step": float(mesh_step),
                    "error_m": float(abs(mesh_step - truth_step)),
                    "width_m": width,
                    "mesh_edge_offset_m": edge_offset,
                }
            )
            if len(profiles) < 12 and abs(truth_step) >= 0.06:
                profiles.append(
                    {
                        "object_id": curb.object_id,
                        "distance": station["distance"],
                        "offsets": offsets.tolist(),
                        "mesh": profile.tolist(),
                        "lidar_sides": truth_h.tolist(),
                    }
                )
            for shift in (-4.0, 4.0):
                control_center = center + shift * normal
                # Controls are off every curb, outside buildings/water, at disjoint support.
                if forbidden.intersects(shapely.Point(control_center)) or curb_geometry.intersects(
                    shapely.Point(control_center)
                ):
                    continue
                control = fit_profile(fitting_surface, control_center, normal, snap=False)
                if control is None:
                    continue
                sample = query(control_center + offsets[:, None] * normal)
                if not np.all(np.isfinite(sample)):
                    continue
                sides = [
                    np.polyfit(offsets[m], sample[m], 1)[1]
                    for m in (
                        (offsets < -0.15) & (offsets >= -0.5),
                        (offsets > 0.15) & (offsets <= 0.5),
                    )
                ]
                controls.append(float(abs(sides[1] - sides[0])))
    strong = [s for s in station_results if abs(s["lidar_step"]) >= 0.06]
    step_pass = ratio([s["error_m"] is not None and s["error_m"] < 0.03 for s in strong])
    errors = [s["error_m"] for s in strong if s["error_m"] is not None]
    median_error = float(np.median(errors)) if errors else None
    p90_error = float(np.quantile(errors, 0.9)) if errors else None
    expected_length = sum(s["length_weight_m"] for s in strong)
    step_length = sum(
        s["length_weight_m"]
        for s in strong
        if s.get("mesh_step") is not None and abs(s["mesh_step"]) >= 0.03
    )
    length_fraction = step_length / expected_length if expected_length else None
    widths = [s["width_m"] for s in strong if s["width_m"] is not None]
    seam = seam_errors(meshes)
    rmse = float(np.sqrt(np.mean(error**2))) if len(error) else None
    coverage = ratio(covered[open_cells])
    control_pass = ratio(np.array(controls) < 0.02)
    metrics = {
        "coverage": {"open_cells": int(open_cells.sum()), "occupied_fraction": coverage},
        "noise_mad_m": noise_mad,
        "lidar": {"heldout_count": len(q), "mesh_hit_count": int(finite.sum()), "rmse_m": rmse},
        "curbs": {
            "heldout_stations": len(station_results),
            "unsupported_heldout_stations": unsupported_stations,
            "strong_stations": len(strong),
            "source_vector_length_m": float(
                sum(
                    line.intersection(shapely.box(*bounds.as_tuple())).length
                    for _, line in source_lines
                )
            ),
            "supported_vector_length_m": sum(s["length_weight_m"] for s in station_results),
            "unsupported_vector_length_m": unsupported_length,
            "all_supported_vector_step_fraction": ratio(
                [abs(s.get("mesh_step", 0)) >= 0.03 for s in station_results]
            ),
            "raised_vector_length_m": expected_length,
            "mesh_step_length_m": step_length,
            "mesh_step_length_fraction": length_fraction,
            "step_error_median_m": median_error,
            "step_error_p90_m": p90_error,
            "step_error_under_3cm_fraction": step_pass,
            "transition_max_m": max(widths) if widths else None,
            "transition_under_20cm_fraction": ratio(np.array(widths) <= 0.2),
            "control_stations": len(controls),
            "control_under_2cm_fraction": control_pass,
        },
        "mesh": {
            "max_tile_triangles": max(len(m.indices) for m in meshes.values()),
            "seam_mismatches": seam,
            "all_triangles_material": all(np.all(m.material > 0) for m in meshes.values()),
        },
    }
    checks = {
        "coverage": coverage is not None and coverage >= 0.95,
        "noise": noise_mad is not None and noise_mad <= 0.015,
        "lidar_rmse": rmse is not None and rmse <= 0.03 and bool(finite.all()),
        "curb_length": length_fraction is not None and length_fraction >= 0.85,
        "curb_steps": median_error is not None
        and median_error <= 0.05
        and len(errors) == len(strong),
        "triangle_budget": metrics["mesh"]["max_tile_triangles"] <= 150_000,
        "seams": not seam,
        "materials": metrics["mesh"]["all_triangles_material"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "stations": station_results,
        "controls_m": controls,
        "profiles": profiles,
    }


def write_report(report, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ground-qa.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    profiles = report.get("profiles", [])
    if profiles:
        fig, axes = plt.subplots(3, 4, figsize=(14, 8), constrained_layout=True)
        for ax, profile in zip(axes.flat, profiles, strict=False):
            ax.plot(profile["offsets"], profile["mesh"], label="Collision mesh")
            ax.scatter(
                [-0.03, 0.03],
                profile["lidar_sides"],
                c="red",
                s=15,
                label="Held-out side intercepts",
            )
            ax.set_title(f"Station {profile['distance']:.0f} m", fontsize=9)
            ax.set_xlabel("Across curb (m)")
            ax.set_ylabel("N2000 height (m)")
        axes.flat[0].legend(fontsize=7)
        fig.savefig(directory / "ground-profiles.png", dpi=130)
        plt.close(fig)
    lines = [
        "# Ground layer QA",
        "",
        "Result: " + ("PASS" if report["passed"] else "FAIL"),
        "",
        "Odd 1 m curb stations are excluded from the even-station breakline fit. "
        "Coordinate-hashed lidar returns (20%) are excluded before ground selection and meshing.",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines += [
        f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in report["checks"].items()
    ]
    lines += [
        "",
        "```json",
        json.dumps(report["metrics"], indent=2),
        "```",
        "",
        f"Build: {report.get('build_seconds', 0):.2f} s. Water: {report.get('water_level', {})}.",
        "",
        "![Held-out curb profiles](ground-profiles.png)",
        "",
    ]
    (directory / "ground-qa.md").write_text("\n".join(lines))
