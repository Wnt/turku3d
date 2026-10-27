"""Low-priority parameter upgrades. One screened panorama description per building, no maps.

The service schedules a single nice(15) child at a time, at most 12 attempts per rolling hour,
only inside the player's ring/cone. Calls stop at 60% quota (unknown quota fails closed).
Reference pixels stay in the sandbox; only validated descriptions enter facade-params/.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from turku3d import config as C
from turku3d.facade_kit import read_description, renderer, store_path, write_description

CALLS_PER_HOUR = 12


def upgrade(inventory: Path, building: str, root: Path) -> dict:
    os.nice(15)
    renderer()  # make the texture package available to a pipeline-only interpreter
    from turkutex import facade_fast as ff
    from turkutex.building_jobs import latest
    from turkutex.codex.runner import Runner, load_jobs
    from turkutex.codex.usage import used_percent
    from turkutex.streetview.facades import load_facades

    quota = used_percent()
    if quota is None or quota >= 60:
        return dict(state="quota-paused", quota=quota)
    if read_description(building):
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
    # Screen all candidates, call for only the first usable wall. Other walls adopt its style.
    job = jobs[0]
    job_path = run / "jobs.jsonl"
    job_path.write_text(json.dumps(job) + "\n")
    result = Runner(
        run / "describe", parallel=1, retries=0, max_pct=60, cache_dir=ff.call_cache()
    ).run(load_jobs(job_path))
    records = latest(run / "describe/out.jsonl")
    record = records.get(job["id"], {})
    d = record.get("answer", {})
    if result or not record.get("ok") or not d.get("wall_visible"):
        return dict(state="description-failed")
    reference = job["meta"]["reference"]
    wall = next(f for f in facades if f.id == d["facade_id"])
    write_description(building, d, reference["source"], reference["crop_sha256"], width=wall.length)
    return dict(state="upgraded", building=building)


class DescribeQueue:
    """A background lane, separate from the draft builder's workers and request lock."""

    def __init__(self, root: Path, builder):
        self.root, self.builder = root, builder
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.state = {"state": "idle", "calls_per_hour": CALLS_PER_HOUR}
        self.retried: dict[str, float] = {}
        self.budget = root / ".facade-describe-budget.json"
        self.thread = threading.Thread(target=self._run, name="facade-describe", daemon=True)
        self.thread.start()

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _status(self, **state):
        with self.lock:
            self.state.update(state)

    def _attempt(self) -> bool:
        try:
            attempts = json.loads(self.budget.read_text())
        except (OSError, ValueError):
            attempts = []
        recent = [t for t in attempts if t > time.time() - 3600]
        self._status(attempts_this_hour=len(recent))
        if len(recent) >= CALLS_PER_HOUR:
            return False
        self.budget.parent.mkdir(parents=True, exist_ok=True)
        temp = self.budget.with_suffix(".tmp")
        temp.write_text(json.dumps([*recent, time.time()]))
        temp.replace(self.budget)
        return True

    def _candidate(self):
        from .service import rank

        with self.builder.lock:
            if self.builder.draining or self.builder.motion is None:
                return None
            motion = self.builder.motion
            tiles = list(self.builder.servable)
        # Existing telemetry priorities are cheap to consume; no session scan in a service request.
        try:
            priority = json.loads((C.data_root() / "telemetry/priority.json").read_text())
            scores = {r["building_id"]: float(r["score"]) for r in priority.get("buildings", [])}
        except (OSError, ValueError, KeyError, TypeError):
            scores = {}
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
                if self.retried.get(key, 0) > time.time() - 3600 or store_path(key).exists():
                    continue
                choices.append((ranked[0], -scores.get(key, 0), ranked[1], key, tile, inventory))
        return min(choices) if choices else None

    def _run(self):
        while not self.closed.wait(15):
            candidate = self._candidate()
            if not candidate:
                self._status(state="idle")
                continue
            if not self._attempt():
                self._status(state="hourly-budget")
                continue
            _, _, _, key, tile, inventory = candidate
            self.retried[key] = time.time()
            self._status(state="describing", building=key)
            try:
                with self.lock:
                    if self.closed.is_set():
                        return
                    self.proc = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "turku3d.coarse.facades",
                            str(inventory),
                            key,
                            str(self.root / ".facade-upgrades"),
                        ],
                        stdout=subprocess.PIPE,
                        start_new_session=True,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    proc = self.proc
                stdout, stderr = proc.communicate(timeout=900)
                if proc.returncode:
                    raise RuntimeError(stderr[-500:])
                result = json.loads(stdout.strip().splitlines()[-1])
                self._status(**result)
                if result["state"] == "upgraded":
                    # Increment the service counter and requeue; resident clients retain the old wall until ready.
                    with self.builder.lock:
                        self.builder.version += 1
                    self.builder.request(tile, 0)
                elif result["state"] == "quota-paused":
                    self.closed.wait(300)
            except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
                if self.proc and self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self._status(state="failed", error=str(exc)[-500:])
            finally:
                with self.lock:
                    self.proc = None

    def stop(self):
        self.closed.set()
        with self.lock:
            if self.proc and self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGTERM)


if __name__ == "__main__":
    print(json.dumps(upgrade(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))))
