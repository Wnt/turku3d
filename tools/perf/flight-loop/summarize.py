"""Condense aligned flight evidence; retain raw per-asset and per-stage observations."""

import collections
import json
import statistics
import sys
from pathlib import Path


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def percentiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return dict(
        count=len(values),
        p50=round(statistics.median(values), 2),
        p90=round(values[min(len(values) - 1, int(len(values) * 0.9))], 2),
        max=round(values[-1], 2),
    )


def summarize(root):
    result = json.loads((root / "result.json").read_text())
    start = result["at"]
    targets = result.get("route", {}).get("targets", ["t0_0_2", "t0_0_3"])
    events = result["events"]
    turn = next((e["at"] for e in events if e["kind"] == "turn"), float("inf"))
    network = rows(root / "network.jsonl")
    responses = [r for r in network if r["kind"] == "response"]
    assets = [
        r
        for r in responses
        if r["at"] >= turn
        and "/coarse/" in r["url"]
        and any(ext in r["url"] for ext in (".glb", ".col", ".ktx2", ".bin"))
    ]

    def source(r):
        return (
            "service-worker-storage"
            if r.get("storedBytes")
            else "http-cache"
            if r.get("disk")
            else r.get("source", "network")
        )

    asset_ids = {r["id"] for r in assets}
    finished = [r for r in network if r["kind"] == "finished" and r["id"] in asset_ids]
    asset_responses = {r["id"]: r for r in assets}
    resources = [
        r
        for r in result.get("resources", [])
        if r.get("at", 0) >= turn
        and "/coarse/" in r["name"]
        and any(ext in r["name"] for ext in (".glb", ".col", ".ktx2", ".bin"))
    ]
    local = rows(root / "host.jsonl")
    cold = next(row["at"] for row in rows(root / "cold.jsonl") if row["kind"] == "cold")
    stages = {tile: [] for tile in targets}
    outcomes = []
    for row in local:
        if row["kind"] == "service":
            try:
                outcome = json.loads(row["line"])
                if outcome.get("job") in targets and "outcome" in outcome:
                    outcomes.append(outcome)
            except ValueError:
                pass
        elif row["kind"] == "stages" and row["tile"] in stages and row["mtime"] >= cold:
            report = row["report"]
            stages[row["tile"]].append(
                dict(
                    at=row["at"],
                    build=report.get("_build"),
                    slowest=sorted(
                        [(k, v.get("seconds", 0)) for k, v in report.items()],
                        key=lambda entry: -entry[1],
                    )[:6],
                )
            )
    summary = dict(
        direction=result.get("direction", "south"),
        done=result["done"],
        targets=targets,
        timeline=[
            dict(
                kind=e["kind"],
                tile=e.get("id", e.get("tile")),
                pass_kind=e.get("pass"),
                seconds=round((e["at"] - start) / 1000, 2),
            )
            for e in events
            if e["kind"] != "visual-collision" or e.get("id") in targets
        ],
        frame_ms=percentiles([r[1] for r in result["frames"]]),
        frame_excluding_capture_ms=percentiles(
            [
                ms
                for at, ms in result["frames"]
                if not any(
                    at >= c["at"] and at - ms <= c.get("end", c["at"])
                    for c in result.get("captures", [])
                )
            ]
        )
        if result.get("captures")
        else None,
        long_tasks_ms=percentiles([r["ms"] for r in result["longTasks"]]),
        return_assets=dict(
            cdp_headers_ms=percentiles([r["ms"] for r in assets]),
            cdp_finished_ms=percentiles([r["ms"] for r in finished]),
            cdp_finished_before_headers=sum(
                r["ms"] < asset_responses[r["id"]]["ms"]
                for r in finished
                if r.get("ms") is not None and asset_responses[r["id"]].get("ms") is not None
            ),
            resource_duration_ms=percentiles([r["duration"] for r in resources]),
            resource_glb_duration_ms=percentiles(
                [r["duration"] for r in resources if ".glb" in r["name"]]
            ),
            sources=dict(collections.Counter(source(r) for r in assets)),
        ),
        peak_heap_mib=max((r.get("heap") or 0 for r in result["samples"]), default=0) / 1024**2,
        heap_after_flight_mib={
            k: v / 1024**2 for k, v in result.get("heapAfterFlight", {}).items() if v is not None
        },
        public={
            route: percentiles(
                [r["ms"] for r in rows(root / "public.jsonl") if r["route"] == route]
            )
            for route in ("coarse/health", "healthz")
        },
        failures=[dict(status=r["status"], url=r["url"]) for r in responses if r["status"] >= 400],
        work={
            kind: percentiles([r["ms"] for r in result["work"] if r["label"] == kind])
            for kind in {r["label"] for r in result["work"]}
        },
        target_outcomes=outcomes,
        stages=stages,
    )
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    compact = {k: v for k, v in summary.items() if k not in ("stages", "failures")}
    compact["failures"] = len(summary["failures"])
    print(json.dumps(compact, indent=2))


if __name__ == "__main__":
    summarize(Path(sys.argv[1]))
