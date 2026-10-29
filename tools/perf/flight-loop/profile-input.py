"""Profile one uncached prepared dataset without changing live producer caches."""

import cProfile
import json
import pstats
import sys
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.inputs import _prepare_one, _sources
from turku3d.coarse.sources import Sources


def main():
    tile = C.VisualTile.parse(sys.argv[1])
    dataset = sys.argv[2]
    if len(sys.argv) > 3:
        from turku3d.coarse import walkable

        exec(compile(Path(sys.argv[3]).read_text(), sys.argv[3], "exec"), vars(walkable))
    paths = _sources(tile, Sources(), first_pass=True)[dataset]
    profiler = cProfile.Profile()
    start = time.monotonic()
    value = profiler.runcall(_prepare_one, tile, dataset, paths)
    from turku3d.stage_cache import content_key

    print(
        json.dumps(
            dict(
                tile=tile.id,
                dataset=dataset,
                seconds=time.monotonic() - start,
                count=len(value),
                content=content_key(value),
            )
        )
    )
    pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(35)


if __name__ == "__main__":
    main()
