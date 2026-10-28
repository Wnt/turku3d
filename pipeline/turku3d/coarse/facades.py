"""Local appearance enrichment followed by quota-limited Astra refinement.

One persistent nice(15) child keeps offline models warm. Reference pixels stay in the
sandbox; only validated descriptions and per-field provenance enter facade-params/.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from turku3d import config as C
from turku3d.facade_kit import read_description, renderer, store_path, write_description

CALLS_PER_HOUR = 12
APPEARANCE_FIELDS = ("wall_colour", "material")
_model = None


def appearance_fields(register: dict, fields: dict) -> tuple[dict, dict]:
    """Accept only measured appearance; absent/abstaining fields retain register values."""
    result = dict(register)
    sources = {field: "register" for field in register}
    for field in APPEARANCE_FIELDS:
        row = fields.get(field, {})
        if row.get("state") == "measured" and row.get("value") is not None:
            result[field] = row["value"]
            sources[field] = "hybrid"
    return result, sources


@lru_cache(maxsize=1)
def recent_priority(directory: Path, minute: int) -> dict:
    """Refresh current play observations on the background lane, at most once a minute."""
    from turku3d.telemetry.priority import prioritize

    now = minute * 60
    today = datetime.fromtimestamp(now)
    paths = [
        directory / (today - timedelta(days=d)).strftime("session-%Y%m%d.jsonl") for d in (1, 0)
    ]
    samples, names = [], []
    for path in paths:
        try:
            with path.open() as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # an append may leave an incomplete trailing line
                    if (
                        isinstance(row, dict)
                        and isinstance(row.get("t"), (int, float))
                        and row["t"] >= now - 86400
                    ):
                        samples.append(row)
            names.append(path.name)
        except OSError:
            continue
    priority = prioritize(names, samples, {}, str(now))
    locations = [
        row
        for row in samples
        if isinstance(row.get("E"), (int, float))
        and isinstance(row.get("N"), (int, float))
        and math.isfinite(row["E"])
        and math.isfinite(row["N"])
    ]
    if locations:
        latest = max(locations, key=lambda row: row["t"])
        priority["player_EN"] = [float(latest["E"]), float(latest["N"])]
    return priority


def telemetry_priority() -> dict:
    directory = C.work_root() / "telemetry"
    fresh = recent_priority(directory, int(time.time() // 60))
    if fresh["sample_count"]:
        return fresh
    try:
        return json.loads((directory / "priority.json").read_text())
    except (OSError, ValueError):
        return {}


def preferred_job(
    jobs: list[dict],
    widths: dict[str, float],
    priority: dict,
    building: str,
    centres: dict[str, tuple[float, float]] | None = None,
) -> dict:
    """Spend a call on the most-seen usable wall, then the closest unseen wall."""
    rows = next(
        (
            r.get("walls", [])
            for r in priority.get("buildings", [])
            if r.get("building_id") == building
        ),
        [],
    )
    scores = {r["facade_id"]: float(r.get("score", 0)) for r in rows}
    player = priority.get("player_EN")

    def distance(fid: str) -> float:
        if (
            not isinstance(player, list)
            or len(player) != 2
            or centres is None
            or fid not in centres
        ):
            return math.inf
        return math.dist(centres[fid], player)

    return min(
        jobs,
        key=lambda job: (
            -scores.get(job["meta"]["facade_id"], 0),
            distance(job["meta"]["facade_id"]),
            -widths.get(job["meta"]["facade_id"], 0),
            job["meta"]["facade_id"],
        ),
    )


def wall_distance(feature: dict, motion: tuple[float, ...]) -> float:
    """Horizontal player distance to a candidate segment in engine metres."""
    (e0, n0), (e1, n1) = feature["geometry"]["coordinates"][:2]
    x0, z0 = e0 - C.ORIGIN_E, C.ORIGIN_N - n0
    x1, z1 = e1 - C.ORIGIN_E, C.ORIGIN_N - n1
    dx, dz = x1 - x0, z1 - z0
    length2 = dx * dx + dz * dz
    t = (
        min(1.0, max(0.0, ((motion[0] - x0) * dx + (motion[1] - z0) * dz) / length2))
        if length2
        else 0.0
    )
    return math.hypot(motion[0] - x0 - t * dx, motion[1] - z0 - t * dz)


def queue_metrics(events: list[dict], now: float) -> dict:
    recent = [r for r in events if r["finished_at"] > now - 3600]
    elapsed = now - max(now - 3600, min((r["started_at"] for r in recent), default=now))
    completed = {
        r["building"] for r in recent if r["state"] == "upgraded" and r["engine"] == "hybrid"
    }
    return dict(
        observation_seconds=round(elapsed, 3),
        hybrid_buildings=len(completed),
        hybrid_buildings_per_minute=60 * len(completed) / elapsed if elapsed > 0 else 0,
        actual_calls_last_hour=sum(r.get("calls") or 0 for r in recent),
        unknown_call_jobs=sum(r.get("calls", 0) is None for r in recent),
    )


def completed(building: str, engine: str) -> bool:
    if not read_description(building):
        return False
    try:
        row = json.loads(store_path(building).read_text())
        # Legacy rows came exclusively from Astra.
        stored = row.get("engine", "astra")
        return stored == "astra" or engine == "hybrid"
    except (OSError, ValueError):
        return False


def upgrade(
    inventory: Path,
    building: str,
    root: Path,
    engine: str = "astra",
    prechecked_usage: int | None = None,
) -> dict:
    renderer()  # make the texture package available to a pipeline-only interpreter
    from turkutex import facade_fast as ff
    from turkutex.building_jobs import latest
    from turkutex.codex.runner import Runner, load_jobs
    from turkutex.codex.usage import used_percent
    from turkutex.streetview.facades import load_facades

    if engine == "astra":
        quota = prechecked_usage if prechecked_usage is not None else used_percent()
        if quota is None or quota >= 60:
            return dict(state="quota-paused", quota=quota)
    if completed(building, engine):
        return dict(state="cached")
    facades = [f for f in load_facades(inventory) if str(f.props["building_id"]) == building]
    if not facades:
        return dict(state="no-walls")
    run = root / store_path(building).stem
    refs = run / "refs"
    run.mkdir(parents=True, exist_ok=True)
    ff.make_refs(facades, refs, ("lookaround", "google", "mapillary"), workers=1, max_views=4)
    jobs, _ = ff.describe_jobs(facades, refs, run)
    if not jobs:
        return dict(state="no-reference")
    job = preferred_job(
        jobs,
        {f.id: f.length for f in facades},
        telemetry_priority(),
        building,
        {f.id: tuple(f.centre) for f in facades},
    )
    if engine == "hybrid":
        import numpy as np
        from PIL import Image
        from turkutex.facade_derive import building_attributes
        from turkutex.facade_hybrid import Engine

        global _model
        if _model is None:
            _model = Engine()
        wall = next(f for f in facades if f.id == job["meta"]["facade_id"])
        height = job["meta"].get("height_m", wall.height_m)
        attrs = wall.props.get("register_attributes")
        if attrs is None:
            attrs = building_attributes(C.raw_dir("wfs/work-3x3"), [building]).get(building, {})
        register = renderer().register_description(wall.id, building, attrs, wall.length, height)
        with Image.open(job["images"][0]) as image:
            _, metadata = _model.measure(
                np.asarray(image.convert("RGB")), wall.id, wall.length, height, attrs
            )
        fields = metadata["fields"]
        for field in APPEARANCE_FIELDS:
            fields.setdefault(field, dict(state="abstain", value=None, reason="no hybrid head"))
        d, sources = appearance_fields(register, fields)
        reference = job["meta"]["reference"]
        write_description(
            building,
            d,
            reference["source"],
            reference["crop_sha256"],
            width=wall.length,
            engine="hybrid",
            field_sources=sources,
            fields=fields,
        )
        return dict(state="upgraded", building=building, engine=engine, calls=0, facade_id=wall.id)
    job_path = run / "jobs.jsonl"
    job_path.write_text(json.dumps(job) + "\n")
    attempts = run / "describe/attempts.jsonl"
    offset = attempts.stat().st_size if attempts.exists() else 0
    result = Runner(
        run / "describe",
        parallel=1,
        retries=0,
        max_pct=60,
        cache_dir=ff.call_cache(),
        allow_unknown_usage=prechecked_usage is not None,
    ).run(load_jobs(job_path))
    calls = 0
    if attempts.exists():
        with attempts.open("rb") as stream:
            stream.seek(offset)
            calls = sum(bool(line.strip()) for line in stream)
    records = latest(run / "describe/out.jsonl")
    record = records.get(job["id"], {})
    d = record.get("answer", {})
    if result or not record.get("ok") or not d.get("wall_visible"):
        return dict(state="description-failed", calls=calls)
    reference = job["meta"]["reference"]
    wall = next(f for f in facades if f.id == d["facade_id"])
    write_description(building, d, reference["source"], reference["crop_sha256"], width=wall.length)
    return dict(
        state="upgraded", building=building, calls=calls, facade_id=job["meta"]["facade_id"]
    )


def worker():
    """Keep model loading off both the draft request and subsequent enrichment jobs."""
    import contextlib

    os.nice(15)
    for line in sys.stdin:
        try:
            inventory, building, root, engine, quota = json.loads(line)
            started = time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                result = upgrade(Path(inventory), building, Path(root), engine, quota)
            result["seconds"] = time.monotonic() - started
        except Exception as exc:
            result = dict(state="failed", error=str(exc)[-500:], calls=None)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker()
    elif sys.argv[1:2] == ["--once"]:
        import contextlib

        with contextlib.redirect_stdout(sys.stderr):
            result = upgrade(
                Path(sys.argv[2]),
                sys.argv[3],
                Path(sys.argv[4]),
                sys.argv[5],
                int(sys.argv[6]),
            )
        print(json.dumps(result))
    else:
        os.nice(15)
        print(
            json.dumps(
                upgrade(
                    Path(sys.argv[1]),
                    sys.argv[2],
                    Path(sys.argv[3]),
                    sys.argv[4] if len(sys.argv) > 4 else "astra",
                )
            )
        )
