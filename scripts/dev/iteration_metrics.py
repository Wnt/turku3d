"""Read-only delivery/check cost report from the host's existing ledgers and logs."""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import subprocess
import time
import tomllib
from pathlib import Path


def rows(path):
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summary(values):
    return (
        {
            "n": len(values),
            "median_s": round(statistics.median(values), 3),
            "max_s": round(max(values), 3),
        }
        if values
        else {"n": 0, "median_s": None, "max_s": None}
    )


def measure(registry, slots, tasks, repo, days=3, now=None):
    since = (time.time() if now is None else now) - days * 86400
    integrations = [r for r in rows(registry / "integrations.jsonl") if r.get("time", 0) >= since]
    landed = [r for r in rows(registry / "landed.jsonl") if r.get("time", 0) >= since]
    waits = [r for r in rows(slots / "waits.jsonl") if r.get("time", 0) >= since]
    published = []
    for row in integrations:
        if row.get("pages", {}).get("status") == "published" and "publish_seconds" in row:
            at = row["time"] - row["wall_seconds"] + row["publish_seconds"]
            ancestors = subprocess.run(
                ["git", "rev-list", row["to"], "^" + row["from"]],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            published.append((at, set(ancestors)))
    latency = []
    for landing in landed:
        if landing.get("class") != "code":
            continue
        matches = [
            at - landing["time"]
            for at, ancestors in published
            if at >= landing["time"] and landing["commit"] in ancestors
        ]
        if matches:
            latency.append(min(matches))
    stages, advisories = collections.defaultdict(list), collections.defaultdict(list)
    specs = collections.defaultdict(lambda: {"runs": 0, "failures": 0, "cached": 0})
    for row in integrations:
        for name, seconds in row.get("stage_seconds", {}).items():
            stages[name].append(seconds)
        for name, seconds in row.get("advisory_seconds", {}).items():
            advisories[name].append(
                (seconds, any(a["stage"] == name for a in row.get("advisory", [])))
            )
        chrome = row.get("chrome") or {}
        for key in chrome.get("run", []):
            specs[key]["runs"] += 1
            specs[key]["failures"] += key in chrome.get("failed", [])
        for key in chrome.get("cached", []):
            specs[key]["cached"] += 1
    durations = json.loads((registry / "spec-durations.json").read_text())
    inventory = sorted(repo.glob("game/tests/**/e2e*/*.spec.ts"))
    inventory += sorted(repo.glob("game/tests/e2e*/*.spec.ts"))
    for file in set(inventory):
        for project in ("webgpu", "webgl"):
            specs[f"{project}:{file.relative_to(repo)}"]
    for key, seconds in durations.items():
        specs[key]["latest_test_seconds"] = seconds
    deep = collections.defaultdict(list)
    for path in registry.glob("integration-*-deep.log"):
        if path.stat().st_mtime < since:
            continue
        for status, name, seconds in re.findall(
            r"^(?:cached )?(PASS|FAIL)\s+(\S+)\s+([\d.]+)s", path.read_text(), re.M
        ):
            deep[name].append((float(seconds), status == "FAIL"))
    config = tomllib.loads((repo / "scripts/dev/gate.toml").read_text())
    quick_names = {
        stage["name"]
        for stage in config["stages"]
        if stage.get("tier") != "deep" and not stage.get("browser") and stage.get("kind") != "e2e"
    } | {"env-sync"}
    quick = collections.defaultdict(list)
    event_waits = {}
    # Raw Codex events have no timestamps; report wait observations, never invented durations.
    for task in tasks.iterdir():
        path = task / "events.jsonl"
        if not path.is_file() or path.stat().st_mtime < since:
            continue
        count = 0
        for event in rows(path):
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "command_execution":
                count += bool(
                    re.search(
                        r"waiting for (?:build|browser) slot", item.get("aggregated_output", "")
                    )
                )
                output = item.get("aggregated_output", "")
                if re.search(r"(?:^|[/\s])gate(?:\s|$)", item.get("command", "")) and re.search(
                    r"^TOTAL\s+(GREEN|RED)", output, re.M
                ):
                    for cached, status, name, seconds in re.findall(
                        r"^(cached )?(PASS|FAIL)\s+(\S+)\s+([\d.]+)s", output, re.M
                    ):
                        if name in quick_names:
                            quick[name].append((float(seconds), status == "FAIL", bool(cached)))
        if count:
            event_waits[task.name] = count
    state = json.loads((slots / "state.json").read_text())
    state_waits = [
        {
            "sandbox": e["sandbox"],
            "kind": e.get("purpose", e.get("lane", e["kind"])),
            "seconds": round((time.time() if now is None else now) - e["since"], 3),
        }
        for e in state["entries"]
        if e["state"] == "waiting"
    ]

    def checks(values):
        return {
            name: {**summary([s for s, _ in entries]), "failures": sum(f for _, f in entries)}
            for name, entries in sorted(values.items())
        }

    return {
        "window_days": days,
        "integrations": len(integrations),
        "landings": len(landed),
        "landing_to_pages": summary(latency),
        "code_landings_without_observed_publication": sum(r.get("class") == "code" for r in landed)
        - len(latency),
        "finish": {
            name: summary([r[name] for r in landed if name in r])
            for name in ("merge_wall_seconds", "lock_wait_seconds", "fast_gate_seconds")
        },
        "source_upload": summary(
            [r["push"]["seconds"] for r in landed if "seconds" in r.get("push", {})]
        ),
        "pages_upload": summary(
            [r["pages"]["seconds"] for r in integrations if "seconds" in r.get("pages", {})]
        ),
        "publication": summary(
            [r["publish_seconds"] for r in integrations if "publish_seconds" in r]
        ),
        "quick_stages_from_task_events": {
            name: {
                **summary([seconds for seconds, _, cached in entries if not cached]),
                "executions": len(entries),
                "cache_hits": sum(c for _, _, c in entries),
                "failures": sum(f for _, f, _ in entries),
            }
            for name, entries in sorted(quick.items())
        },
        "build_stages": {name: summary(values) for name, values in sorted(stages.items())},
        "slot_waits": {
            kind: summary([r["wait_seconds"] for r in waits if r["kind"] == kind])
            for kind in sorted({r["kind"] for r in waits})
        },
        "current_waiters": state_waits,
        "chrome_specs": dict(sorted(specs.items())),
        "deep_stages": checks(deep),
        "advisories": checks(advisories),
        "task_commands_observing_slot_waits": event_waits,
        "limitations": [
            "Publication time reconstructed as ledger time - wall_seconds + publish_seconds; only commits in each published from..to batch count.",
            "Chrome runtime is the latest recorded test seconds, including retries, not suite wall time.",
            "Failure counts are observed executions, including repeats of known failures; missing/interrupted results are unknown.",
            "state.json is a current snapshot; no historical state archive exists.",
            "Raw Codex events have no timestamps; quick-stage history covers event files modified in the window, not timestamped invocations. Reported stage durations are measured command output.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=3)
    args = parser.parse_args()
    home = Path.home() / "turku3d"
    print(
        json.dumps(
            measure(
                home / "wt/.registry",
                home / "host-slots",
                home / "tasks",
                Path(__file__).resolve().parents[2],
                args.days,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
