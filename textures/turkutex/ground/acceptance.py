"""Honest real-data acceptance audit, separate from small offline unit tests."""

import argparse
import json
from pathlib import Path

from ..post.pipeline import ktx2_header, sha


def audit(root: Path, qa: Path) -> list[str]:
    problems = []

    def read(path):
        if not path.exists():
            problems.append(f"missing {path}")
            return {}
        return json.loads(path.read_text())

    manifest = read(root / "ground/MANIFEST.json")
    library = read(root / "library/MANIFEST.json")
    marking = read(root / "ground/markings.report.json")
    renders = read(qa)
    seams = read(root / "ground/seams.json")
    if seams and not seams["pass"]:
        problems.append("source-grid tile seam check failed")
    if manifest:
        if set(manifest["tiles_written"]) != {"t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"}:
            problems.append("four Phase 1 texture tiles are required")
        if not manifest["gpu_budget"]["within_D4"]:
            problems.append("ground texture GPU allocation exceeds D4")
        for tile in manifest["tiles"]:
            if not tile["ground_inputs"]:
                problems.append(f"{tile['tile']}: W1-01 ground triangle material inputs absent")
            for texture in tile["textures"].values():
                path = root / "ground" / tile["tile"] / texture["uri"]
                header = ktx2_header(path) if path.exists() else {}
                if not header.get("valid") or header.get("levels", 0) < 2:
                    problems.append(f"{path}: missing mipmapped KTX2")
            if tile["pixel_m"] > 0.04:
                problems.append(f"{tile['tile']}: output spacing exceeds 4 cm")
    if library and len(library["materials"]) != 8:
        problems.append("eight library ground materials required")
    if marking:
        for kind in ["crosswalk", "stop_line"]:
            if not marking["counts"].get(kind):
                problems.append(f"{kind}: no verified vector paint emitted")
        if not (marking["counts"].get("lane_solid") or marking["counts"].get("lane_dashed")):
            problems.append("lane lines: no verified vector paint emitted")
    if renders:
        for render in renders:
            path = qa.parent / render["file"]
            if not path.exists() or render.get("sha256") != sha(path):
                problems.append(f"capture missing or checksum mismatch: {path}")
        captures = {(r["backend"], r["view"], r["before"]) for r in renders if not r["errors"]}
        for backend in ["WebGPUBackend", "WebGLBackend"]:
            for view in range(3):
                for before in [False, True]:
                    if (backend, view, before) not in captures:
                        problems.append(f"missing render {backend} view={view} before={before}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--qa", type=Path, required=True)
    a = ap.parse_args(argv)
    problems = audit(a.root, a.qa)
    for problem in problems:
        print(f"BLOCKED: {problem}")
    if not problems:
        print(
            "PASS: ground texture deliverable audit; visual sign-off still requires screenshot review"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
