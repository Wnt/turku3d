"""Run video captures through the host scheduler with each brief's allowance.

The task runner parses only a BROWSER_BUDGET_MIN assignment; this brief states the
allowance in prose. This adapter retains the shared ledger, one browser slot and
normal debiting, and changes only this task's documented allowance.
"""
import os
import json
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/dev'))
import browser_budget
import host_slots

if os.environ.get('TURKU3D_SANDBOX') not in ('media-pipeline-video-v2', 'showcase-video-v3'):
    raise SystemExit('This allowance belongs only to the v2 and showcase-v3 video sandboxes')
usage_env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'textures')}
usage = json.loads(subprocess.check_output([sys.executable, '-m', 'turkutex.codex.usage'], env=usage_env))
if usage.get('used_percent') is None or usage['used_percent'] >= 80:
    raise SystemExit(f'Video quota stop: {usage.get("used_percent")} percent weekly usage')
original = browser_budget.budget_minutes
browser_budget.budget_minutes = lambda task, *args, **kwargs: (
    (60.0 if task == 'showcase-video-v3' else 150.0) if task in ('media-pipeline-video-v2', 'showcase-video-v3') else original(task, *args, **kwargs)
)
sys.argv = ['host_slots.py', 'browser', '--', *sys.argv[1:]]
raise SystemExit(host_slots.main())
