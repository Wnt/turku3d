"""Wall time and memory of a build's stages, measured while they run.

A build says where its time and its memory go: every stage is logged when it ends, with the memory of the build's whole
process tree at its highest during that stage, and the manifest keeps the table (`timings.stages`).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import contextmanager


def tree_mb(root: int | None = None) -> float:
    """Memory of a process and its descendants, MB. On Linux the proportional share (a page that forked workers
    share counts once); elsewhere resident memory, which counts it in each."""
    root = root or os.getpid()
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            pid, parent, kb = (int(v) for v in parts)
            rss[pid] = kb
            children.setdefault(parent, []).append(pid)
    total, todo = 0, [root]
    while todo:
        pid = todo.pop()
        total += _share_kb(pid, rss.get(pid, 0))
        todo += children.get(pid, [])
    return total / 1024.0


def _share_kb(pid: int, rss_kb: int) -> int:
    try:
        with open(f"/proc/{pid}/smaps_rollup") as rollup:
            for line in rollup:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return rss_kb


class Meter:
    """`with meter.stage(name):` records the stage's seconds and the tree's peak memory during it."""

    def __init__(self, log=print, interval: float = 1.0):
        self.log = log
        self.started = time.perf_counter()
        self.stages: dict[str, dict] = {}
        self.peak_mb = 0.0
        self._current: str | None = None
        self._stage_peak = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, args=(interval,), daemon=True)
        self._thread.start()

    def _sample(self, interval: float) -> None:
        while not self._stop.is_set():
            now = tree_mb()
            self.peak_mb = max(self.peak_mb, now)
            self._stage_peak = max(self._stage_peak, now)
            self._stop.wait(interval)

    @contextmanager
    def stage(self, name: str):
        self._current, self._stage_peak = name, tree_mb()
        began = time.perf_counter()
        try:
            yield
        finally:
            seconds = time.perf_counter() - began
            peak = max(self._stage_peak, tree_mb())
            self.peak_mb = max(self.peak_mb, peak)
            self.stages[name] = {"s": round(seconds, 2), "peakMb": round(peak)}
            self.log(f"  [{time.perf_counter() - self.started:7.1f} s] {name:<20} {seconds:7.2f} s, peak {peak:6.0f} MB")

    def close(self) -> None:
        self._stop.set()
