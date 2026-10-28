"""Summarize measured area and capture time without counting abstentions as scores."""
from collections import Counter
import json
import os
from pathlib import Path

from gmaps_round3 import ROOT, atomic_json, browser_budget

root = ROOT / 'synthetic'
coverage = json.loads((root/'coverage.json').read_text())['quarters']
records = {}
for key in coverage:
    file = root/'captures'/key/'capture.json'
    if file.exists():
        records[key] = json.loads(file.read_text())
scored = {key: value for key,value in coverage.items() if value['status']=='scored'}
masked = sum(json.loads(Path(value['report']).read_text()).get('accepted_gap_mask_cells',0) for value in scored.values())
area = sum((value['extent_en'][2]-value['extent_en'][0])*(value['extent_en'][3]-value['extent_en'][1]) for value in scored.values())
seconds = sum(session['seconds'] for record in records.values() for session in record.get('session_statistics',[]))
account = browser_budget.read(os.environ['TURKU3D_SANDBOX'])
original_keys = {row['coverage_id'] for row in json.loads((Path.home()/'turku3d/private/gmaps-data/round2/plan.json').read_text())['quarters']}
result = dict(planned=len(coverage), eligible=sum(record.get('inventory_state')=='eligible' for record in records.values()),
    scored=len(scored), states=dict(Counter(value['status'] for value in coverage.values())),
    scored_nominal_area_km2=area/1e6, excluded_missing_node_footprint_m2=masked,
    capture_core_seconds=seconds, capture_core_seconds_per_quarter=seconds/len(records) if records else None,
    total_task_browser_minutes=account['used_minutes'], browser_budget_minutes=account['budget_minutes'],
    original_seeded_scored=sum(value['status']=='scored' for key,value in coverage.items() if key in original_keys),
    findings=str(root/'findings.md'),
    abstentions=[dict(coverage_id=key, reason=value.get('reason')) for key,value in coverage.items() if value['status']=='abstained'])
atomic_json(root/'summary.json',result)
print(json.dumps(result,indent=2))
