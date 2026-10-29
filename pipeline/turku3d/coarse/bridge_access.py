"""Mapped stair access and suspended landing extents, independent of deck meshing."""

import numpy as np
import shapely


def configure(bridge, ways, members, foot):
    from .bridges import PATHS

    endpoints = shapely.union_all([member["line"].boundary for member in members])
    bridge["stair_access"] = (
        foot
        and any(w["tags"].get("highway") not in PATHS for w in bridge["under"])
        and any(
            step["tags"].get("highway") == "steps"
            and min(
                [
                    step["line"].boundary.distance(endpoints),
                    *(step["line"].boundary.distance(w["line"]) for w in bridge["approaches"]),
                ]
            )
            < 0.05
            for step in ways
        )
    )
    if bridge["stair_access"]:
        # Bridge areas can include access flights. Their envelope axis must not
        # turn the stairs into a deck or invalidate its lidar end stations.
        main = max(members, key=lambda w: w["line"].length)
        bridge["line"] = main["line"]
        if "outline" in bridge:
            bridge["outline"] = bridge["outline"].intersection(
                main["line"].buffer(bridge["width"] / 2, cap_style="flat", join_style="mitre")
            )


def landing_length(way, endpoint, direction):
    """Steps own their climb; a short continuation is a suspended landing."""
    joined = [
        w
        for w in way.get("approaches", [])
        if w["line"].boundary.distance(shapely.Point(endpoint)) < 0.05
    ]
    if any(w["tags"].get("highway") == "steps" for w in joined):
        return 0.0
    distances = [
        float(np.max((np.asarray(w["line"].coords) - endpoint) @ direction))
        for w in joined
        if w["line"].length <= 10
    ]
    return max([0.0, *distances])


def cached_stage(cache, tile, grid_pos, grid_idx, materials, wet, inputs):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    from . import bridges
    from .build import bridge_decks
    from .stages import deck_stage

    base = Path(__file__).parents[1]
    return cache.run(
        "bridges",
        source_key(
            deck_stage,
            bridge_decks,
            Path(C.__file__),
            base / "col/materials_v1.json",
            Path(__file__),
            Path(bridges.__file__),
            *(
                Path(__file__).with_name(name + ".py")
                for name in ("tunnels", "bridge_details", "bridge_join", "geometry")
            ),
            base / "structures/geometry.py",
            base / "structures/layer.py",
            base / "bridges/build.py",
        ),
        [
            tile,
            grid_pos,
            grid_idx,
            materials,
            wet,
            inputs.points,
            inputs.roads,
            [b.footprint for b in inputs.buildings],
        ],
        lambda: deck_stage(tile, grid_pos, grid_idx, materials, wet, inputs),
    )
