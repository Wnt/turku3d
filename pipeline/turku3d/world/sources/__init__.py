"""Source readers of the world pipeline: area reads, with missing data fetched on demand into the shared cache."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from functools import cached_property

from ..store import data_root
from .ortho import Ortho
from .osm import Osm
from .terrain import Terrain
from .wfs import Wfs


class Sources:
    def __init__(self, cache: Path | None = None, log=print):
        self.cache = cache or data_root() / "sources"
        self.log = log
        self.timings: dict[str, float] = {}
        self.wfs = Wfs(self.cache / "wfs", self)
        self.terrain = Terrain(self.cache, self)
        self.osm = Osm(self.cache / "osm", self)
        self.ortho = Ortho(self.cache / "ortho", self)

    @cached_property
    def panoramas(self):
        """Street panoramas of both providers around a place: `spots(box)` lists them, fetching the coverage
        that is missing (the observation chain's reader, turku3d.observe.panoramas)."""
        from ...observe.panoramas import Panoramas

        return Panoramas()

    @contextmanager
    def timed(self, name: str):
        """Accumulates the wall time spent reading or fetching one source."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.perf_counter() - started
