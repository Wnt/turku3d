"""Resolve only observed metadata and return its advertised missing requests."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pipeline'))
sys.path.insert(0, os.environ['GMAPS_TOOLS'])
from detail import metadata

record = metadata(Path(sys.argv[1]))
print(json.dumps(dict(
    complete=record['complete'],
    metadata_complete=record['metadata_complete'],
    available=record['terminal_data_nodes'], loaded=record['terminal_loaded'],
    missing=len(record['required_missing_terminal_nodes']),
    missing_fraction=record['required_missing_fraction'],
    frontier=record['unresolved_metadata_frontier'],
    nodes=record['required_missing_terminal_nodes'],
)))
