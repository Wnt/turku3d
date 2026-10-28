"""Attribute every >100 ms scenario interval using recorded work and the matching Chrome trace."""
import collections
import gzip
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
phase = sys.argv[2] if len(sys.argv) > 2 else "drive"
threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 100
state = json.loads((root / "scenario.json").read_text())
anchor_now = json.loads((root / "last-state.json").read_text())["air"]["traceAnchor"]
trace = root / phase / "trace.json.gz"
anchor = None
threads = {}
starts = {}
for line in gzip.open(trace, "rt"):
    if '"air-scenario-anchor"' not in line and '"thread_name"' not in line and '"name":"Profile"' not in line:
        continue
    try:
        e = json.loads(line.strip().rstrip(","))
    except ValueError:
        continue
    if e["name"] == "Profile":
        starts[e["pid"], e["id"]] = e["args"]["data"]["startTime"]
    elif e["name"] == "air-scenario-anchor":
        anchor = e
    elif e["name"] == "thread_name":
        threads[e["pid"], e["tid"]] = e["args"]["name"]
if anchor is None:
    raise RuntimeError("No scenario trace anchor")
rows = []
for frame in state["frames"]:
    if frame[2] != phase or frame[1] <= threshold:
        continue
    end = anchor["ts"] + (frame[0] - anchor_now) * 1000
    rows.append({"rafMs": frame[0], "intervalMs": frame[1], "start": end - frame[1] * 1000,
                 "end": end, "trace": [], "samples": collections.Counter(), "gc": []})
main = anchor["pid"], anchor["tid"]
clocks, nodes, parents = dict(starts), {}, {}
for line in gzip.open(trace, "rt"):
    if '"dur":' not in line and '"Profile' not in line:
        continue
    try:
        e = json.loads(line.strip().rstrip(","))
    except ValueError:
        continue
    key = e.get("pid"), e.get("tid")
    if key == main and e.get("name") in ("Profile", "ProfileChunk"):
        profile = (e["pid"], e["id"])
        data = e["args"]["data"]
        if e["name"] == "Profile":
            continue
        cp = data.get("cpuProfile", {})
        for n in cp.get("nodes", []):
            node = (*profile, n["id"])
            cf = n["callFrame"]
            nodes[node] = (cf.get("functionName") or "(anonymous)") + "@" + cf.get("url", "").split("/")[-1].split("?")[0]
            if "parent" in n:
                parents[node] = (*profile, n["parent"])
            for child in n.get("children", []):
                parents[(*profile, child)] = node
        t = clocks.get(profile, data.get("startTime", e["ts"]))
        for sample, delta in zip(cp.get("samples", []), data.get("timeDeltas", [])):
            t += delta
            for row in rows:
                if row["start"] <= t <= row["end"]:
                    row["samples"][(*profile, sample)] += 1
        clocks[profile] = t
    elif e.get("ph") == "X" and e.get("dur", 0) >= 500:
        if key != main and threads.get(key) != "CrGpuMain":
            continue
        for row in rows:
            overlap = max(0, min(row["end"], e["ts"] + e["dur"]) - max(row["start"], e["ts"]))
            if not overlap:
                continue
            row["trace"].append({"thread": threads.get(key), "name": e["name"], "overlapMs": overlap / 1000})
            if key == main and any(tag in e["name"] for tag in ("MinorGC", "MajorGC", "V8.GC")):
                row["gc"].append((max(row["start"], e["ts"]), min(row["end"], e["ts"] + e["dur"])))
for row in rows:
    start_now = row["rafMs"] - row["intervalMs"]
    row["work"] = [w for w in state["work"] if w["start"] <= row["rafMs"] and w["start"] + w["ms"] >= start_now]
    row["builders"] = [b for b in state["builders"] if start_now <= b["now"] <= row["rafMs"]]
    samples = collections.Counter()
    for node, count in row.pop("samples").items():
        stack = []
        while node in nodes and len(stack) < 12:
            stack.append(nodes[node]); node = parents.get(node)
        samples[" < ".join(stack)] += count
    row["sampleStacks"] = samples.most_common(8)
    row["trace"] = sorted(row["trace"], key=lambda e: -e["overlapMs"])[:16]
    spans = sorted(row.pop("gc")); total = 0; end = -1
    for a, b in spans:
        total += max(0, b - max(a, end)); end = max(end, b)
    row["gcMs"] = total / 1000
    row.pop("start"); row.pop("end")
out = root / f"{phase}-hitches-{threshold:g}ms.json"
out.write_text(json.dumps(rows, indent=2))
print(json.dumps({"thresholdMs": threshold, "frames": len(rows), "output": str(out)}))
