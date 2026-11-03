"""The ordered migration chain. Append new migrations at the end; never reorder or reuse a key.

Each entry runs at the end of every generator build and over existing farm products."""

from . import chain_record, floodlights, stand_roofs

CHAIN = (
    chain_record.MIGRATION,
    stand_roofs.MIGRATION,
    floodlights.MIGRATION,
)
