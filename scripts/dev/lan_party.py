"""LAN freeze reminder, frozen cache coverage watch, and bounded ten-player test."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import lan_freeze
from lan_test import snapshot

HERE = Path(__file__).resolve().parent
DEADLINE = "2026-10-30T09:00:00+02:00"


def message(subject, body, target="orchestrator", kind="note"):
    subprocess.run(
        [str(HERE / "codex-task"), "msg", target, "--kind", kind, "--subject", subject, body],
        check=True,
    )


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def summary(row):
    return json.dumps({k: row[k] for k in ("commit", "code", "coverage", "disk_free", "timings")})


def watch(output, deadline):
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "watch-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    end = datetime.fromisoformat(deadline).timestamp()
    next_snapshot = 0
    while time.time() < end:
        try:
            marker = json.loads(lan_freeze.remote("cat " + lan_freeze.MARKER))
            cutoff = datetime.fromisoformat(marker["freeze_at"]).timestamp()
            if time.time() >= cutoff - 1800 and state.get("reminded_cutoff") != cutoff:
                message(
                    "LAN generator freeze in 30 minutes",
                    "Cutoff "
                    + marker["freeze_at"]
                    + ". No OCI generator deployments after cutoff. "
                    "Client-only Pages fixes require a ten-player smoke. Readiness owns frozen commit and cache evidence.",
                    target="all",
                )
                state["reminded_cutoff"] = cutoff
                save(state_path, state)
            if time.time() >= cutoff:
                frozen = lan_freeze.capture()
                save(output / "freeze.json", frozen)
                if not state.get("frozen"):
                    message(
                        "LAN generator frozen; prewarm watch started",
                        json.dumps(frozen)
                        + " Existing idle scheduler owns all four workers and yields to players. No separate bulk builder.",
                    )
                    state["frozen"] = frozen["commit"]
                    save(state_path, state)
                if time.time() >= next_snapshot:
                    row = snapshot()
                    save(output / "latest.json", row)
                    with (output / "coverage.jsonl").open("a") as stream:
                        stream.write(json.dumps(row) + "\n")
                    print(summary(row), flush=True)
                    if row["commit"] != frozen["commit"] or row["code"] != frozen["code"]:
                        raise RuntimeError(
                            "Frozen generator identity changed; warmed coverage is invalid"
                        )
                    low_disk = (
                        row["disk_free"]["/"] < 5 * 1024**3
                        or row["disk_free"]["/srv/turku3d"] < 15 * 1024**3
                    )
                    if low_disk and not state.get("disk_alerted"):
                        message("LAN prewarm low disk", summary(row), kind="request")
                        state["disk_alerted"] = True
                    prebuild = row["health"].get("prebuild", {})
                    if prebuild.get("state") == "running" and not state.get("warming"):
                        message("LAN prewarm started", summary(row))
                        state["warming"] = True
                    wider, routes = row["coverage"]["wider"], row["coverage"]["routes"]
                    if wider["cached"] >= wider["total"] / 2 and not state.get("half"):
                        message("LAN prewarm 50%", summary(row))
                        state["half"] = True
                    if routes["cached"] + routes["unavailable"] == routes[
                        "total"
                    ] and not state.get("routes_done"):
                        message(
                            "LAN mission corridor complete",
                            summary(row)
                            + "; unavailable source tiles are reported separately, not counted as built.",
                        )
                        state["routes_done"] = True
                    done = wider["cached"] + wider["unavailable"] == wider["total"]
                    if done and not state.get("done"):
                        message("LAN prewarm done", summary(row))
                        state["done"] = True
                    next_snapshot = time.time() + 120
                    save(state_path, state)
                # Leave 15 minutes before the report deadline even if full warming is
                # impossible. The test explicitly fails its warmed criterion in that case.
                if "test_exit" not in state and (
                    state.get("routes_done") or time.time() >= end - 900
                ):
                    test_output = output / "ten-player"
                    result = subprocess.run(
                        [sys.executable, str(HERE / "lan_test.py"), "--output", str(test_output)]
                    )
                    state["test_exit"] = result.returncode
                    save(state_path, state)
                    result_path = test_output / "result.json"
                    body = (
                        result_path.read_text()
                        if result_path.exists()
                        else "No complete test receipt; inspect " + str(test_output)
                    )
                    message(
                        "LAN ten-player test " + ("PASS" if result.returncode == 0 else "BLOCKED"),
                        body,
                    )
                    if state.get("done"):
                        return result.returncode
                if state.get("done") and "test_exit" in state:
                    return state["test_exit"]
            save(state_path, state)
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            subprocess.CalledProcessError,
        ) as error:
            detail = (
                error.stderr[-2000:]
                if isinstance(error, subprocess.CalledProcessError) and error.stderr
                else str(error)
            )
            print(detail, file=sys.stderr, flush=True)
            if state.get("last_error") != detail:
                message("LAN readiness watch blocker", detail, kind="request")
                state["last_error"] = detail
                save(state_path, state)
        time.sleep(30)
    message(
        "LAN readiness deadline reached",
        "Final warming/test state: " + json.dumps(state) + "; receipts " + str(output),
        kind="note" if state.get("test_exit") == 0 else "request",
    )
    return state.get("test_exit", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "watch"])
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ["TURKU3D_WORK"]) / "reports/lan-party"
    )
    parser.add_argument("--deadline", default=DEADLINE)
    args = parser.parse_args()
    if args.action == "snapshot":
        row = snapshot()
        save(args.output / "latest.json", row)
        print(summary(row))
        return 0
    return watch(args.output, args.deadline)


if __name__ == "__main__":
    raise SystemExit(main())
