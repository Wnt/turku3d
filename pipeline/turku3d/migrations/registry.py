"""The ordered migration chain. Append new migrations at the end; never reorder or reuse a key.

Each entry runs at the end of every generator build and over existing farm products."""

from . import (bridge_ends, chain_record, finished_ground, floodlights, furniture, landmark_windows,
               monolith_walls, path_trees, racks, roadbed, shell_look, signals, signs, span_lamps, stand_roofs,
               structure_materials, trees_lidar, wires, facade_reality)

CHAIN = (
    chain_record.MIGRATION,
    stand_roofs.MIGRATION,
    # wires before floodlights: floodlights imports the whole source table (coarse.sources), so nearly every
    # pipeline commit changes its code hash, and each step's generation chains on the one before it. With wires
    # first, its generation (and the pass's wires@2 checkpoint, ~130 s CPU a tile) survives such commits. The two
    # steps commute: each adds only its own key to the tile's lights.
    wires.MIGRATION,
    floodlights.MIGRATION,
    trees_lidar.MIGRATION,
    furniture.MIGRATION,
    racks.MIGRATION,
    landmark_windows.MIGRATION,
    # Measures the HD models as the steps above left them: facade changes must come before it.
    shell_look.MIGRATION,
    signals.MIGRATION,
    signs.MIGRATION,
    # Ground and deck rebuild of the tiles that build a bridge, then the roadbed over every tile's lanes.
    bridge_ends.MIGRATION,
    roadbed.MIGRATION,
    # Moves and drops trunks: re-measures the trees-lidar crowns of its final placement.
    path_trees.MIGRATION,
    # Spot rebuild of the tiles whose ground the finished-ground rule changes (ground.finished).
    finished_ground.MIGRATION,
    # Spot rebuild of the tiles building a cultural monolith whose unphotographed walls were blank.
    monolith_walls.MIGRATION,
    # Spot rebuild of the tiles with tunnels, bridges, stairs or retaining walls: procedural PBR materials.
    structure_materials.MIGRATION,
    # Rebuild of every tile with buildings: source-driven facade material, opening and colour rules.
    facade_reality.MIGRATION,
    # Lamps where a lighting span wire the survey saw without one crosses a lit street.
    span_lamps.MIGRATION,
)
