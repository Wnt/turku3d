"""Every reviewed generated facade candidate, accepted or rejected, kept beyond its sandbox.

The `visualqa` stage of `facade-batch` overwrites `<run>/generated/<facade_id>.png` on every
retry and the run itself is removed with its sandbox at landing. After each review this module
copies the reviewed wall into the shared data root, where the localhost viewer
(`tools/dev/facadeCandidates.mjs`, `GET /facades/candidates/`) shows it:

  $TURKU3D_DATA/facade-approvals/candidates/<task>/
    index.json                 {task, updated, counts, walls: [{facade_id, building_id, position,
                                tile, candidates: [<n>.json records]}]}
    <facade_id>/<n>.png        the reviewed wall: generated pixels only, never a reference crop
                               or a street photo
    <facade_id>/<n>.json       {n, image, sha256, accepted, review, generation, audit, kept_at}

`n` counts the distinct images of a wall within a task (1, 2, ...): a rerun that reviews the same
bytes again updates that candidate instead of adding one. `accepted` is the reviewer's verdict
together with the acceptance audit (`facade_generated_qa`) once it has run. Nothing is pruned.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from PIL import Image
from turku3d import config

from .building_jobs import latest
from .post.pipeline import sha
from .streetview.facades import Facade, load_facades

#: Spawn distance in front of a wall (m): on the street, facing the wall.
SPAWN_DISTANCE_M = 8.0
#: The image prompt kept per candidate is cut to this many characters.
PROMPT_CHARS = 2000


def root() -> Path:
    return config.data_root() / "facade-approvals" / "candidates"


def default_task() -> str:
    return os.environ.get("TURKU3D_SANDBOX") or "main"


def review_problems(answer: dict | None, facade_id: str) -> list[str]:
    """Why the fixed reviewer's answer (facade_visual_qa.schema.json) rejects a wall; [] accepts."""
    answer = answer or {}
    problems = []
    if answer.get("facade_id") != facade_id or not answer.get("grid_matches"):
        problems.append("window or ground-opening grid mismatch")
    if answer.get("upper_openings") != answer.get("expected_upper_openings"):
        problems.append("upper opening count mismatch")
    if answer.get("storeys") != answer.get("expected_storeys"):
        problems.append("storey count mismatch")
    if not answer.get("orthographic"):
        problems.append("generated image is not an orthographic elevation")
    if not answer.get("clean") or answer.get("foreign_objects"):
        problems.append("large foreground or foreign object detected")
    return problems


def _write(path: Path, value) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(path)


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn last line while another stage appends
    return rows


def position(facade: Facade) -> dict:
    """Wall centre, a spawn on the street in front of it looking at it, and its L0 tile."""
    e, n = (float(v) for v in facade.centre)
    se, sn = (float(v) for v in facade.centre + facade.normal * SPAWN_DISTANCE_M)
    x, _, z = config.en_to_local(e, n)
    return {
        "E": round(e, 2),
        "N": round(n, 2),
        "spawn": [round(se, 2), round(sn, 2), round((facade.normal_azimuth_deg + 180) % 360, 1)],
        "tile": config.VisualTile.at(x, z).id,
    }


def _generation(record: dict | None) -> dict:
    if not record:
        return {}
    meta = record.get("meta") or {}
    calls = record.get("imagegen_calls") or []
    image_prompt = next((c.get("prompt") for c in calls if c.get("prompt")), None)
    width, height = meta.get("width_m"), meta.get("height_m")
    return {
        "generate_key": record.get("key"),
        "model": record.get("model"),
        "effort": record.get("effort"),
        "seconds": record.get("seconds"),
        "finished_at": record.get("finished_at"),
        "views_used": meta.get("views_used"),
        "width_m": width,
        "height_m": height,
        "aspect": round(width / height, 4) if width and height else None,
        "retry_feedback": meta.get("feedback"),
        "prompt_summary": meta.get("prompt_summary"),
        "image_prompt": image_prompt[:PROMPT_CHARS] if image_prompt else None,
    }


def _candidates(wall: Path) -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in wall.glob("*.json")), key=lambda c: c["n"])


def keep(run: Path, facades_path: Path, task: str, dest: Path) -> list[dict]:
    """Copy every wall the run's latest reviews saw into `dest/<task>/`; returns the touched records.

    A review counts only when it inspected the bytes now in `<run>/generated/<facade_id>.png`
    (the same proof the acceptance audit requires), so a failed or stale review keeps nothing.
    """
    facades = {f.id: f for f in load_facades(facades_path)}
    generate = {r.get("key"): r for r in _records(run / "run_generate/out.jsonl")}
    task_dir = dest / task
    touched = []
    for record in latest(run / "run_visualqa/out.jsonl").values():
        meta = record.get("meta") or {}
        fid = meta.get("facade_id")
        if not record.get("ok") or meta.get("kind") != "generated_visual_qa" or not fid:
            continue
        source = run / "generated" / f"{fid}.png"
        inputs = {i["name"]: i["sha256"] for i in record.get("inputs", [])}
        if not source.is_file() or inputs.get(source.name) != (digest := sha(source)):
            continue
        wall = task_dir / fid
        wall.mkdir(parents=True, exist_ok=True)
        existing = _candidates(wall)
        candidate = next((c for c in existing if c["sha256"] == digest), None)
        if candidate is None:
            number = max((c["n"] for c in existing), default=0) + 1
            shutil.copyfile(source, wall / f"{number}.png")
            with Image.open(source) as image:
                size = image.size
            facade = facades.get(fid)
            candidate = {
                "n": number,
                "task": task,
                "facade_id": fid,
                "building_id": str(facade.props.get("building_id", fid)) if facade else None,
                "image": f"{number}.png",
                "sha256": digest,
                "width_px": size[0],
                "height_px": size[1],
                "position": position(facade) if facade else None,
                "generation": _generation(generate.get(meta.get("generate_key"))),
                "audit": None,
                "kept_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        problems = review_problems(record.get("answer"), fid)
        candidate["review"] = {
            "accepted": not problems,
            "problems": problems,
            "answer": record.get("answer"),
            "model": record.get("model"),
            "effort": record.get("effort"),
            "thread_id": record.get("thread_id"),
            "finished_at": record.get("finished_at"),
        }
        _settle(candidate)
        _write(wall / f"{candidate['n']}.json", candidate)
        touched.append(candidate)
    if touched:
        write_index(task_dir)
    return touched


def _settle(candidate: dict) -> None:
    audit = candidate.get("audit")
    candidate["accepted"] = candidate["review"]["accepted"] and (audit is None or audit["accepted"])


def record_audit(task_dir: Path, rows: list[dict]) -> int:
    """Attach acceptance-audit rows ({facade_id, sha256, accepted, problems}) to the candidates
    with those exact bytes; returns how many candidates changed."""
    changed = 0
    for row in rows:
        wall = task_dir / row["facade_id"]
        for candidate in _candidates(wall) if wall.is_dir() else []:
            if candidate["sha256"] != row.get("sha256"):
                continue
            candidate["audit"] = {"accepted": row["accepted"], "problems": row["problems"]}
            _settle(candidate)
            _write(wall / f"{candidate['n']}.json", candidate)
            changed += 1
    if changed:
        write_index(task_dir)
    return changed


def write_index(task_dir: Path) -> dict:
    walls = []
    for wall in sorted(p for p in task_dir.iterdir() if p.is_dir()):
        candidates = _candidates(wall)
        if not candidates:
            continue
        last = candidates[-1]
        walls.append(
            {
                "facade_id": wall.name,
                "building_id": last.get("building_id"),
                "position": last.get("position"),
                "accepted": any(c["accepted"] for c in candidates),
                "candidates": candidates,
            }
        )
    every = [c for w in walls for c in w["candidates"]]
    index = {
        "task": task_dir.name,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": {
            "walls": len(walls),
            "candidates": len(every),
            "accepted": sum(c["accepted"] for c in every),
            "rejected": sum(not c["accepted"] for c in every),
        },
        "walls": walls,
    }
    _write(task_dir / "index.json", index)
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", choices=["keep"])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--facades", type=Path, required=True)
    ap.add_argument("--task", default=default_task())
    a = ap.parse_args(argv)
    kept = keep(a.run, a.facades, a.task, root())
    print(json.dumps({"task": a.task, "kept": len(kept), "dir": str(root() / a.task)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
