"""Agent-owned first/full OCI builds and shipped-surface evidence for the flicker fix."""
import json
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/dev"))
from facade_publish import remote


def health():
    with urllib.request.urlopen("https://turku.lab.madekivi.fi/coarse/health", timeout=4) as response:
        return json.load(response)["build_health"]


started = time.monotonic()
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
before = health()
results = []
for tile in ("t0_2_1", "t0_2_0"):
    built = json.loads(subprocess.check_output([
        sys.executable, "scripts/dev/probe_drafts.py", "--commit", commit,
        "--tile", tile, "--first-budget", "90", "--full-budget", "180",
    ], text=True, timeout=min(290, 570 - (time.monotonic() - started))))
    manifest = json.loads(remote("cat " + shlex.quote(built["output"] + "/manifest.json")))
    visual = manifest["visual"][tile]
    audit = visual["wall_surfaces"]
    assert audit["triangles"] == visual["tris"], (tile, "not all shipped triangles audited")
    assert audit["program_triangles"] > 0 and audit["instances"] > 0
    assert {"buildings", "props", "vegetation"} <= audit["layers"].keys()
    built["wall_surfaces"] = audit
    results.append(built)
    print(json.dumps(dict(tile=tile, passes=built["passes"], wall_surfaces=audit)), flush=True)
    assert audit["violations"] == 0, (tile, audit["overlaps"][:10])
after = health()
summary = dict(commit=commit, seconds=round(time.monotonic()-started, 3), builds=results,
               health_before=before, health_after=after)
print(json.dumps(summary), flush=True)
# Rolling windows can expire events; successful own probes must not introduce failures.
for kind in ("first", "full"):
    assert after["passes"][kind]["failed"] <= before["passes"][kind]["failed"], (kind, "new failures")
    assert after["passes"][kind]["cancelled"] <= before["passes"][kind]["cancelled"], (kind, "new cancellations")
old, new = before["passes"]["full"]["p50_seconds"], after["passes"]["full"]["p50_seconds"]
assert old is None or new is None or new <= max(old, 30), ("full p50 worsened", old, new)
