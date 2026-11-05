"""The ordered migration chain. Append new migrations at the end; never reorder or reuse a key.

Each entry runs at the end of every generator build and over existing farm products."""

from . import (bridge_ends, chain_record, floodlights, furniture, landmark_windows, path_trees, racks, roadbed,
               shell_look, signals, signs, stand_roofs, trees_lidar, wires)

CHAIN = (
    chain_record.MIGRATION,
    stand_roofs.MIGRATION,
    floodlights.MIGRATION,
    wires.MIGRATION,
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
)
