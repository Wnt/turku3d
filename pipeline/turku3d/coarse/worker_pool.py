"""Persistent draft generator workers, recycled after source edits or bounded use."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from turku3d import config as C


class Workers:
    """One persistent ``python -m turku3d.coarse worker`` per builder thread: the interpreter, the
    producer's imports and the node packager are paid once, not per tile (seconds of wall time each
    on a loaded host). A worker is started again when it died, after MAX_BUILDS tiles (bounded
    memory), and when a pipeline source file changed since it started, so code edits take effect."""

    MAX_BUILDS = 50
    SOURCES = ("pipeline/turku3d/**/*.py", "pipeline/turku3d/package/*.mjs")

    def __init__(self):
        self.local = threading.local()
        self.procs: list[subprocess.Popen] = []
        self.lock = threading.Lock()
        self.jobs = {}

    def _changed_since(self, started: float) -> bool:
        return any(
            p.stat().st_mtime > started
            for pattern in self.SOURCES
            for p in C.REPO_ROOT.glob(pattern)
        )

    def _worker(self, background=False) -> subprocess.Popen:
        w = getattr(self.local, "worker", None)
        if w and w["proc"].poll() is None and w["builds"] < self.MAX_BUILDS:
            if not getattr(w["proc"], "deprioritized", False) and not self._changed_since(w["started"]):
                return w["proc"]
        if w:
            self._end(w["proc"])
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "turku3d.coarse",
                "worker",
            ],
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "VECLIB_MAXIMUM_THREADS": "1",
                "RAYON_NUM_THREADS": "1",
                "ARROW_IO_THREADS": "1",
                "VIPS_CONCURRENCY": "1",
            },
        )
        self.local.worker = {
            "proc": proc,
            "builds": 0,
            "started": time.time(),
            "background": background,
        }
        with self.lock:
            self.procs.append(proc)
        return proc

    def __call__(self, tile: str, out: Path, *, first_pass=False) -> subprocess.CompletedProcess:
        proc = self._worker(background=not first_pass)
        with self.lock:
            self.jobs[tile] = proc
        self.local.worker["builds"] += 1
        try:
            proc.stdin.write(
                json.dumps({"tile": tile, "out": str(out), "first_pass": first_pass}) + "\n"
            )
            proc.stdin.flush()
            line = proc.stdout.readline()
        except OSError:
            line = ""
        with self.lock:
            self.jobs.pop(tile, None)
        if not line:
            self._end(proc)
            self.local.worker = None
            return subprocess.CompletedProcess([], 1, "", "the build worker exited")
        r = json.loads(line)
        return subprocess.CompletedProcess([], r["code"], r.get("stdout", ""), r.get("stderr", ""))

    def deprioritize(self, tile):
        with self.lock:
            proc = self.jobs.get(tile)
            if proc is not None and proc.poll() is None and hasattr(os, 'setpriority'):
                try:
                    os.setpriority(os.PRIO_PGRP, proc.pid, 19)
                    proc.deprioritized = True
                except ProcessLookupError:
                    pass

    def cancel(self, tile):
        with self.lock:
            proc = self.jobs.get(tile)
            if proc is None or proc.poll() is not None:
                return False
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return False
            return True

    def _end(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        with self.lock:
            if proc in self.procs:
                self.procs.remove(proc)

    def close(self) -> None:
        with self.lock:
            procs = list(self.procs)
        for proc in procs:
            self._end(proc)
