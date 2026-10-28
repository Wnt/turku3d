"""Private metadata planner adapter. Only native-verified visits influence history."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ['GMAPS_TOOLS'])
from detail import metadata, next_views
from pyproj import Transformer
from storage import atomic_json

capture = Path(sys.argv[2])
record = json.loads((capture / 'capture.json').read_text())
for visit in record.get('detail_visits', []):
    # The capture accepts only <5 m native-point errors before recording a visit.
    if visit.get('timing', {}).get('target_error_m', 999) >= 5:
        raise ValueError('Unverified visit in round 3 planner history')
transform = Transformer.from_crs(4326, 3877, always_xy=True)
for visit in record.get('detail_visits', []):
    point = visit['timing']['verified_visible_point']
    visit.setdefault('requested_E', visit['E'])
    visit.setdefault('requested_N', visit['N'])
    visit['E'], visit['N'] = transform.transform(point['lon'], point['lat'])
    # Target verification is independent of whether the network became quiet.
    visit['mode'] = 'verified native visible point in close 3D'
atomic_json(capture / 'capture.json', record)
report = metadata(capture)
plan = next_views(capture)
plan['frontier'] = len(report['unresolved_metadata_frontier'])
plan['summary']['required_missing'] = len(report['required_missing_terminal_nodes'])
plan['summary']['missing_fraction'] = report['required_missing_fraction']
plan['observed_history'] = record.get('detail_visits', [])
print(json.dumps(plan))
