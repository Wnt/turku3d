"""Source readers of the world pipeline: area reads, with missing data fetched on demand into the shared cache."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from ..store import data_root
from .terrain import Terrain
from .wfs import Wfs


class Sources:
    def __init__(self, cache: Path | None = None, log=print):
        self.cache = cache or data_root() / "sources"
        self.log = log
        self.timings: dict[str, float] = {}
        self.wfs = Wfs(self.cache / "wfs", self)
        self.terrain = Terrain(self.cache, self)

    @contextmanager
    def timed(self, name: str):
        """Accumulates the wall time spent reading or fetching one source."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.perf_counter() - started
