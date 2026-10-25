"""Tile ids <-> bbox <-> LAZ tiles. The implementation lives in turku3d.config (single source of
truth); this module re-exports it under the name used in docs/ARCHITECTURE.md §3."""

from turku3d.config import (  # noqa: F401
    COLLISION_TILE_SIZE,
    LAZ_TILE_SIZE,
    VISUAL_TILE_SIZE,
    BBoxEN,
    BBoxLocal,
    CollisionTile,
    LazTile,
    VisualTile,
    collision_tiles_for_bbox,
    grid_bbox,
    grid_index,
    grid_range,
    laz_tiles_for_bbox,
    visual_tiles_for_bbox,
)
