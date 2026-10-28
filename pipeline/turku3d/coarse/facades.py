"""Local appearance enrichment followed by quota-limited Astra refinement.

One persistent nice(15) child keeps offline models warm. Reference pixels stay in the
sandbox; only validated descriptions and per-field provenance enter facade-params/.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from turku3d import config as C
from turku3d.facade_kit import read_description, renderer, store_path, write_description

CALLS_PER_HOUR = 12
APPEARANCE_FIELDS = ("wall_colour", "trim_colour", "material")
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
    return prioritize(names, samples, {}, str(now))


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
    jobs: list[dict], widths: dict[str, float], priority: dict, building: str
) -> dict:
    """Spend a call on the most-seen usable wall, with width as the unseen-wall fallback."""
    rows = next(
        (
            r.get("walls", [])
            for r in priority.get("buildings", [])
            if r.get("building_id") == building
        ),
        [],
    )
    scores = {r["facade_id"]: float(r.get("score", 0)) for r in rows}
    return min(
        jobs,
        key=lambda job: (
            -scores.get(job["meta"]["facade_id"], 0),
            -widths.get(job["meta"]["facade_id"], 0),
            job["meta"]["facade_id"],
        ),
    )


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
    ff.make_refs(facades, refs, ("lookaround", "google", "mapillary"), workers=1, max_views=1)
    jobs, _ = ff.describe_jobs(facades, refs, run)
    if not jobs:
        return dict(state="no-reference")
    job = preferred_job(jobs, {f.id: f.length for f in facades}, telemetry_priority(), building)
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


class DescribeQueue:
    """A background lane, separate from the draft builder's workers and request lock."""

    def __init__(self, root: Path, builder):
        self.root, self.builder = root, builder
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.state = {"state": "idle", "calls_per_hour": CALLS_PER_HOUR}
        self.retried: dict[tuple[str, str], float] = {}
        self.next_astra = 0.0
        self.prefer_astra = False
        self.child_log = None
        self.budget = root / ".facade-describe-budget.json"
        self.progress = root / ".facade-progress.jsonl"
        self.events = []
        if self.progress.exists():
            for line in self.progress.read_text().splitlines():
                try:
                    event = json.loads(line)
                    if event["finished_at"] > time.time() - 3600:
                        self.events.append(event)
                except (ValueError, KeyError, TypeError):
                    continue
        self.thread = threading.Thread(target=self._run, name="facade-describe", daemon=True)
        self.thread.start()

    def status(self) -> dict:
        with self.lock:
            return dict(self.state, **queue_metrics(self.events, time.time()))

    def _status(self, **state):
        with self.lock:
            self.state.update(state)

    def _attempt(self) -> int | None:
        try:
            attempts = json.loads(self.budget.read_text())
        except (OSError, ValueError):
            attempts = []
        recent = [t for t in attempts if t > time.time() - 3600]
        self._status(attempts_this_hour=len(recent))
        if len(recent) >= CALLS_PER_HOUR:
            return None
        from turkutex.codex.usage import used_percent

        quota = used_percent()
        if quota is None or quota >= 60:
            self._status(state="quota-paused", quota=quota)
            return None
        self.budget.parent.mkdir(parents=True, exist_ok=True)
        temp = self.budget.with_suffix(".tmp")
        temp.write_text(json.dumps([*recent, time.time()]))
        temp.replace(self.budget)
        return quota

    def _candidate(self, engine="hybrid"):
        from .service import rank

        with self.builder.lock:
            if self.builder.draining or self.builder.motion is None:
                return None
            motion = self.builder.motion
            tiles = list(self.builder.servable)
        # Existing telemetry priorities are cheap to consume; no session scan in a service request.
        scores = {
            r["building_id"]: float(r["score"]) for r in telemetry_priority().get("buildings", [])
        }
        choices = []
        for tile in tiles:
            ranked = rank(tile, 0, motion)
            if ranked is None or ranked[0] > 1:
                continue
            inventory = self.root / tile / "facade-candidates.json"
            try:
                features = json.loads(inventory.read_text())["features"]
            except (OSError, ValueError, KeyError):
                continue
            for f in features:
                key = str(f["properties"]["building_id"])
                if self.retried.get((key, engine), 0) > time.time() - 3600 or completed(
                    key, engine
                ):
                    continue
                order = (
                    (-scores.get(key, 0), ranked[0])
                    if engine == "astra"
                    else (ranked[0], -scores.get(key, 0))
                )
                choices.append((*order, ranked[1], key, tile, inventory))
        return min(choices) if choices else None

    def _describe(self, inventory, key, engine, prechecked_usage=None):
        if engine == "astra":
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".facade-astra.log").open("a") as log:
                done = subprocess.run(
                    [
                        str(C.REPO_ROOT / ".venv/bin/python"),
                        "-m",
                        "turku3d.coarse.facades",
                        "--once",
                        str(inventory),
                        key,
                        str(self.root / ".facade-upgrades"),
                        engine,
                        str(prechecked_usage),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    timeout=900,
                    start_new_session=True,
                )
            if done.returncode:
                raise RuntimeError(f"Astra facade worker exited {done.returncode}")
            return json.loads(done.stdout)
        with self.lock:
            if self.closed.is_set():
                return dict(state="stopped")
            if self.proc is None:
                self.root.mkdir(parents=True, exist_ok=True)
                self.child_log = (self.root / ".facade-worker.log").open("a")
                self.proc = subprocess.Popen(
                    # Keep Torch out of the native geo environment's conflicting OpenMP runtime.
                    [
                        str(C.REPO_ROOT / ".venv/bin/python"),
                        "-m",
                        "turku3d.coarse.facades",
                        "--worker",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self.child_log,
                    start_new_session=True,
                    text=True,
                    bufsize=1,
                )
            proc = self.proc
        proc.stdin.write(
            json.dumps(
                [str(inventory), key, str(self.root / ".facade-upgrades"), engine, prechecked_usage]
            )
            + "\n"
        )
        proc.stdin.flush()
        if not select.select([proc.stdout], [], [], 900)[0]:
            raise TimeoutError("facade worker exceeded 900 seconds")
        return json.loads(proc.stdout.readline())

    def _run(self):
        while not self.closed.wait(1):
            engine = "hybrid"
            candidate = None
            quota = None
            if self.prefer_astra and time.time() >= self.next_astra:
                candidate = self._candidate("astra")
                quota = self._attempt() if candidate else None
                if candidate and quota is not None:
                    engine = "astra"
                else:
                    candidate = None
                    self.next_astra = time.time() + 60
            if candidate is None:
                candidate = self._candidate("hybrid")
            self.prefer_astra = engine == "hybrid"
            if not candidate:
                self._status(state="idle")
                self.closed.wait(14)
                continue
            _, _, _, key, tile, inventory = candidate
            self.retried[key, engine] = time.time()
            self._status(state="describing", building=key, engine=engine)
            started = time.time()
            try:
                result = self._describe(inventory, key, engine, quota)
                self._record(started, key, engine, result)
                self._status(**result)
                if result["state"] == "upgraded":
                    with self.builder.lock:
                        self.builder.version += 1
                    self.builder.request(tile, 0)
                elif result["state"] == "quota-paused":
                    self.next_astra = time.time() + 300
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                self.stop_child()
                result = dict(state="failed", error=str(exc)[-500:], calls=None)
                self._record(started, key, engine, result)
                self._status(**result)

    def _record(self, started, building, engine, result):
        event = dict(
            result, started_at=started, finished_at=time.time(), building=building, engine=engine
        )
        self.events = [r for r in self.events if r["finished_at"] > event["finished_at"] - 3600]
        self.events.append(event)
        self.progress.parent.mkdir(parents=True, exist_ok=True)
        with self.progress.open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        self._status(**queue_metrics(self.events, event["finished_at"]))

    def stop_child(self):
        with self.lock:
            if self.proc:
                if self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
                self.proc.stdin.close()
                self.proc.stdout.close()
                self.proc = None
            if self.child_log:
                self.child_log.close()
                self.child_log = None

    def stop(self):
        self.closed.set()
        self.stop_child()


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
