"""Run video captures through the host scheduler with the brief's 150-minute allowance.

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

if os.environ.get('TURKU3D_SANDBOX') != 'media-pipeline-video-v2':
    raise SystemExit('This allowance belongs only to media-pipeline-video-v2')
usage_env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'textures')}
usage = json.loads(subprocess.check_output([sys.executable, '-m', 'turkutex.codex.usage'], env=usage_env))
if usage.get('used_percent') is None or usage['used_percent'] >= 80:
    raise SystemExit(f'Video quota stop: {usage.get("used_percent")} percent weekly usage')
original = browser_budget.budget_minutes
browser_budget.budget_minutes = lambda task, *args, **kwargs: (
    150.0 if task == 'media-pipeline-video-v2' else original(task, *args, **kwargs)
)
sys.argv = ['host_slots.py', 'browser', '--', *sys.argv[1:]]
raise SystemExit(host_slots.main())
