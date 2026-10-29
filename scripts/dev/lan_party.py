"""Warm-up-only LAN freeze reminder, deferred delivery and cache coverage observer."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import lan_freeze

HERE = Path(__file__).resolve().parent
DEADLINE = "2026-10-30T09:00:00+02:00"
QUIET_UNTIL = "2026-10-29T23:00:00+00:00"
LOCAL_CHECKS = "bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_idle.py pipeline/tests/test_coarse_unavailable.py"


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


def wait_until(when):
    until = datetime.fromisoformat(when).timestamp()
    while (remaining := until - time.time()) > 0:
        time.sleep(min(30, remaining))


def snapshot():
    if time.time() < datetime.fromisoformat(QUIET_UNTIL).timestamp():
        raise RuntimeError("OCI observer is quiet until 23:00 UTC for Jonni's own tests")
    command = "timeout --kill-after=2 75 /home/ubuntu/turku3d-drafts/run.sh python -c "
    return json.loads(lan_freeze.remote(command + shlex.quote((HERE / "lan_host.py").read_text())))


def summary(row):
    result = {k: row[k] for k in ("commit", "code", "coverage", "disk_free", "memory", "timings")}
    if "observed_progress" in row:
        result["observed_progress"] = row["observed_progress"]
    return json.dumps(result)


def deliver_after(output, result):
    save(output / "delivery-state.json", dict(state="quiet", until=QUIET_UNTIL, pid=os.getpid()))
    wait_until(QUIET_UNTIL)
    command = [
        str(HERE / "codex-task"),
        "finish",
        "lan-party-readiness",
        "--result",
        str(result),
        "--keep-sandbox",
        "--test",
        LOCAL_CHECKS,
        "--test",
        "python3 -m unittest scripts/dev/tests/test_build_health.py scripts/dev/tests/test_lan_freeze.py",
        "--async-test",
        LOCAL_CHECKS,
        "--async-reason",
        "Warm-up-only scope: local scheduler/coverage regressions; no OCI probes, browser or load runs during Jonni's tests.",
    ]
    with (output / "delivery.log").open("w") as stream:
        code = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode
    save(
        output / "delivery-state.json",
        dict(state="delivered" if code == 0 else "blocked", exit=code, pid=os.getpid()),
    )
    if code:
        message(
            "Warm-up delivery needs attention",
            "finish failed; inspect " + str(output / "delivery.log"),
            kind="request",
        )
    else:
        message(
            "Warm-up-only slice delivered",
            "Local checks and finish complete; freeze observer remains active. No load/browser tests scheduled.",
        )
    return code


def watch(output, deadline):
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "watch-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    end = datetime.fromisoformat(deadline).timestamp()
    next_snapshot = 0
    wait_until(QUIET_UNTIL)
    while time.time() < end:
        try:
            marker = json.loads(lan_freeze.remote("cat " + lan_freeze.MARKER))
            cutoff = datetime.fromisoformat(marker["freeze_at"]).timestamp()
            if time.time() >= cutoff - 1800 and state.get("reminded_cutoff") != cutoff:
                message(
                    "LAN generator freeze in 30 minutes",
                    "Cutoff "
                    + marker["freeze_at"]
                    + ". No OCI generator deploys afterward. Readiness owns warm-up only; Jonni's tests own LAN acceptance.",
                    target="all",
                )
                state["reminded_cutoff"] = cutoff
            if time.time() >= cutoff:
                frozen = lan_freeze.capture()
                save(output / "freeze.json", frozen)
                if not state.get("frozen"):
                    message(
                        "LAN generator frozen; prewarm observer started",
                        json.dumps(frozen)
                        + " Ordinary low-priority scheduler owns warming; player demand wins. No separate builder or load test.",
                    )
                    state["frozen"] = frozen["commit"]
                if time.time() >= next_snapshot:
                    row = snapshot()
                    if row["commit"] != frozen["commit"] or row["code"] != frozen["code"]:
                        raise RuntimeError(
                            "Frozen generator identity changed; warmed coverage is invalid"
                        )
                    wider, routes = row["coverage"]["wider"], row["coverage"]["routes"]
                    baseline = state.setdefault(
                        "coverage_baseline", dict(at=row["at"], cached=wider["cached"])
                    )
                    elapsed, gained = (
                        row["at"] - baseline["at"],
                        wider["cached"] - baseline["cached"],
                    )
                    if elapsed >= 120 and gained >= 4:
                        row["observed_progress"] = dict(
                            tiles_per_minute=round(60 * gained / elapsed, 3),
                            eta_seconds={
                                p: round(
                                    (c["total"] - c["cached"] - c["unavailable"]) * elapsed / gained
                                )
                                for p, c in row["coverage"].items()
                            },
                        )
                    save(output / "latest.json", row)
                    with (output / "coverage.jsonl").open("a") as stream:
                        stream.write(json.dumps(row) + "\n")
                    print(summary(row), flush=True)
                    low_disk = (
                        row["disk_free"]["/"] < 5 * 1024**3
                        or row["disk_free"]["/srv/turku3d"] < 15 * 1024**3
                    )
                    if low_disk and not state.get("disk_alerted"):
                        message("LAN prewarm low disk", summary(row), kind="request")
                        state["disk_alerted"] = True
                    if row["health"].get("prebuild", {}).get(
                        "state"
                    ) == "running" and not state.get("warming"):
                        message("LAN prewarm started", summary(row))
                        state["warming"] = True
                    if wider["cached"] >= wider["total"] / 2 and not state.get("half"):
                        message("LAN prewarm 50%", summary(row))
                        state["half"] = True
                    if routes["cached"] + routes["unavailable"] == routes[
                        "total"
                    ] and not state.get("routes_done"):
                        message("LAN mission corridor complete", summary(row))
                        state["routes_done"] = True
                    if wider["cached"] + wider["unavailable"] == wider["total"]:
                        message("LAN prewarm done", summary(row))
                        state["done"] = True
                    next_snapshot = time.time() + 120
                    save(state_path, state)
                    if state.get("done"):
                        return 0
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
                message("LAN warm-up observer blocker", detail, kind="request")
                state["last_error"] = detail
                save(state_path, state)
        time.sleep(30)
    message(
        "LAN warm-up deadline reached",
        "Final cache state " + json.dumps(state) + "; receipts " + str(output),
        kind="request",
    )
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "watch", "deliver-after"])
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ["TURKU3D_WORK"]) / "reports/lan-party"
    )
    parser.add_argument("--deadline", default=DEADLINE)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.action == "snapshot":
        row = snapshot()
        save(args.output / "latest.json", row)
        print(summary(row))
        return 0
    if args.action == "deliver-after":
        if args.result is None:
            parser.error("--result is required for deliver-after")
        deliver_after(args.output, args.result)
    return watch(args.output, args.deadline)


if __name__ == "__main__":
    raise SystemExit(main())
