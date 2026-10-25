"""Ortho coverage map and reproducible Markdown/JSON evidence."""

from __future__ import annotations

import json

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from turku3d import config as C


def write_report(output, report, vectors, confirmed, added, unsupported, bbox):
    ortho = C.raw_dir("ortho") / "ortho2022to/work-3x3.vrt"
    width = 1400
    height = round(width * (bbox.zmax - bbox.zmin) / (bbox.xmax - bbox.xmin))
    with rasterio.open(ortho) as src:
        window = from_bounds(*bbox.to_en().as_tuple(), transform=src.transform)
        rgb = src.read(
            [1, 2, 3], window=window, out_shape=(3, height, width), resampling=Resampling.bilinear
        )
    image = Image.fromarray(np.moveaxis(rgb, 0, -1).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    scale = width / (bbox.xmax - bbox.xmin)

    def line(coords, color, stroke=2):
        xy = [((x - bbox.xmin) * scale, (z - bbox.zmin) * scale) for x, z, *_ in coords]
        if len(xy) > 1:
            draw.line(xy, fill=color, width=stroke)

    for _, geom in vectors:
        line(geom.coords, "#f5d742", 4)
    for feature in confirmed:
        line(feature["centers_xz"], "#46e989" if feature["kind"] == "curb" else "#7cc8ff", 3)
    for coords in unsupported:
        line(coords, "#ff645f", 3)
    for feature in added:
        line(feature["centers_xz"], "#fa65ed", 3)
    draw.rectangle((8, 8, 690, 70), fill="#151b25")
    draw.text((18, 16), "LIDAR RELIEF | 2021 lidar / 2022 ortho | north up", fill="white")
    draw.text(
        (18, 35),
        "Green: raised vector | Blue: drop/quay | Red: unsupported | Magenta: lidar-only",
        fill="white",
    )
    draw.text(
        (18, 51),
        f"Support {report['confirmed_fraction']:.1%}; evidence is not mesh-step coverage",
        fill="white",
    )
    image.save(output / "coverage-map.jpg", quality=90)
    (output / "coverage.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    rows = "\n".join(
        f"| {key} | {value:.1f} |" for key, value in report["added_lidar_length_m"].items()
    )
    median = report["median_step_m"]
    p90 = report["p90_step_m"]
    text = f"""# Lidar relief: {report["scope"]}

Measured from {report["points"]:,} input points ({report["ground_points"]:,} selected ground points).
Extent: engine x/z {report["bbox_local"]}; raster {report["resolution_m"]} m. Elapsed {report["elapsed_s"]:.1f} s.

| Kantakartta curb metric | Metres |
|---|---:|
| Total source length | {report["vector_length_m"]:.1f} |
| Supported two-sided profiles | {report["supported_length_m"]:.1f} |
| Raised relief at least 3 cm | {report["raised_length_m"]:.1f} |
| Measured flush / dropped spans | {report["flush_length_m"]:.1f} |
| Unsupported | {report["unsupported_length_m"]:.1f} |

Length-weighted profile confirmation: **{report["confirmed_fraction"]:.1%}**.
This includes measured flush/drop spans and is not the fraction with a real mesh step.
Unsupported samples break lines; no inferred heights bridge gaps.
Length-weighted median absolute step: {median}; p90: {p90} metres.

| Lidar-only relief class | Added metres |
|---|---:|
{rows}

Lidar-only lines start from raster discontinuities, subtract the 0.8 m mapped-curb corridor,
building interiors (+0.3 m) and water, and require raw-point slope fits on both sides,
confidence >= 0.25, step >= 5 cm and surviving measured polylines at least 2 m long.
Fitting split: {report.get("fitting_split", "not recorded")}.
Classes are geometric hypotheses: a tall edge is not independently verified as a quay,
and wall/planter and bench/planter semantics require later review.
{report["small_object_candidates"]} typed small-object candidates are in objects.json.

![Relief coverage over 2022 true ortho](coverage-map.jpg)

Ground integration / before-after mesh coverage: {report["mesh_coverage"]}.
No geometry coverage is claimed until actual ground layer meshes are rebuilt and measured.

Reproduce from the worktree, after `source ../sandbox.env`:
`bash pipeline/env/run.sh python -m turku3d.lidar relief --scope {report["scope"]} --resolution {report["resolution_m"]}`.

Code hash: `{report["code_hash"]}`. Source identity: `{report["source_key"]}`.
Full parameters, sources and numbers: coverage.json. Ground input: breaklines.json.
"""
    (output / "coverage.md").write_text(text)
