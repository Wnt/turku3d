"""The ordered migration chain. Append new migrations at the end; never reorder or reuse a key.

Each entry runs at the end of every generator build and over existing farm products."""

from . import chain_record, floodlights, furniture, stand_roofs, trees_lidar, wires

CHAIN = (
    chain_record.MIGRATION,
    stand_roofs.MIGRATION,
    floodlights.MIGRATION,
    wires.MIGRATION,
    trees_lidar.MIGRATION,
    furniture.MIGRATION,
)
