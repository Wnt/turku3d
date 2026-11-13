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
        self.clip: tuple[float, float, float, float] | None = None  # a build's box: lidar readers keep only this
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

    def prepare(self, box: tuple[float, float, float, float], jobs: int | None = None) -> dict:
        """Makes ahead what the readers of a survey box would otherwise make one by one on first use: the lidar
        products of its tiles (lidar.py). From then on the lidar readers keep only the box in memory."""
        from . import lidar

        self.clip = (box[0] - lidar.RING, box[1] - lidar.RING, box[2] + lidar.RING, box[3] + lidar.RING)
        return lidar.prepare(box, self.log, jobs)

    def release(self) -> None:
        """Drops what the readers hold in memory for discover (lidar tiles and windows): compile needs none of it."""
        from ..green import lidar as green
        from ..street import lidar as street

        self.__dict__.pop("_object_lidar", None)
        green.tile.cache_clear()
        street.tile.cache_clear()
        self.terrain.grids.clear()
        self.terrain.points.clear()

    @contextmanager
    def timed(self, name: str):
        """Accumulates the wall time spent reading or fetching one source."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.perf_counter() - started
