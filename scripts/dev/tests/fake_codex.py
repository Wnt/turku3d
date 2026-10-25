#!/usr/bin/env python3
"""Protocol fake: tests exercise the real CLI and process lifecycle, without model calls."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1] == "app-server":
    for line in sys.stdin:
        msg = json.loads(line)
        result = (
            {}
            if msg["method"] == "initialize"
            else {
                "rateLimits": {
                    "primary": {
                        "usedPercent": None
                        if os.environ.get("FAKE_QUOTA") == "unknown"
                        else int(os.environ.get("FAKE_QUOTA", "1"))
                    }
                }
            }
        )
        print(json.dumps({"id": msg["id"], "result": result}), flush=True)
else:
    prompt = sys.stdin.read()
    print(json.dumps({"type": "thread.started", "thread_id": "fake-session-123"}), flush=True)
    if "resume" in sys.argv:
        assert "fake-session-123" in sys.argv
    assert os.environ["TURKU3D_WORK"].endswith("/work")
    assert "gpt-6-astra" in sys.argv
    assert "danger-full-access" in sys.argv
    if os.environ.get("FAKE_SLEEP"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print(json.dumps({"type": "fake.child", "pid": child.pid}), flush=True)
        time.sleep(60)
    out = Path(sys.argv[sys.argv.index("-o") + 1])
    out.write_text("## Result: DONE\n\n" + prompt[-100:])
    print(json.dumps({"type": "turn.completed"}), flush=True)
