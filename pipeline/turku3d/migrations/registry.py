"""The ordered migration chain. Append new migrations at the end; never reorder or reuse a key.

Each entry runs at the end of every generator build and over existing farm products."""

from . import chain_record, floodlights, furniture, landmark_windows, racks, shell_look, stand_roofs, trees_lidar, wires

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
)
