"""One ten-minute LAN investigation, including host-slot waits and child cleanup."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_health import smoke_alerts
from facade_publish import HOST, SSH, remote

HERE = Path(__file__).resolve().parent
HOME = "/home/ubuntu/turku3d-drafts"


def snapshot():
    command = "timeout --kill-after=2 75 " + HOME + "/run.sh python -c "
    return json.loads(remote(command + shlex.quote((HERE / "lan_host.py").read_text())))


def run_test(output):
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    (output / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    commit = before["commit"]
    source = f"{HOME}/sources/{commit}"
    # Use the existing HTTP driver's exact directed graph routing without modifying it.
    code = f"""
import runpy,json
from urllib.request import urlopen
m=runpy.run_path({source + "/tools/remote-drafts/ten-drivers.py"!r})
with urlopen('http://127.0.0.1:7764/coarse/navigation.json',timeout=10) as r:nav=json.load(r)
print(json.dumps(m['routes'](nav['roads'],10)))
"""
    routes = remote(
        f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} {HOME}/run.sh python -c " + shlex.quote(code)
    )
    (output / "routes.json").write_text(routes + "\n")
    remote_output = "/srv/turku3d/work/lan-party/ten-drivers-" + str(time.time_ns()) + ".json"
    driver = [
        "timeout",
        "--kill-after=2",
        "400",
        f"{source}/tools/remote-drafts/run.sh",
        "python",
        f"{source}/tools/remote-drafts/ten-drivers.py",
        "--drivers",
        "10",
        "--seconds",
        "120",
        "--speed-kmh",
        "50",
        "--output",
        remote_output,
    ]
    commands = {
        "drivers": [
            *SSH,
            HOST,
            f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + shlex.join(driver),
        ],
        "sockets": [
            "node",
            "scripts/dev/lan-sockets.mjs",
            str(output / "routes.json"),
            str(output / "sockets.json"),
            "300",
            "50",
        ],
        "arrival": [sys.executable, "scripts/dev/probe_tile_arrival.py", "--warmed-cache"],
    }
    telemetry = """
import json,time,pathlib
from urllib.request import urlopen
end=time.monotonic()+305
while time.monotonic()<end:
 memory={line.split(':')[0]:int(line.split()[1])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
 row=dict(at=time.time(),available=memory['MemAvailable'],swap_used=memory['SwapTotal']-memory['SwapFree'],cpu=pathlib.Path('/proc/stat').read_text().splitlines()[0])
 try:
  with urlopen('http://127.0.0.1:7764/coarse/health',timeout=2) as r:row['health']=json.load(r)
  with urlopen('http://127.0.0.1:7700/healthz',timeout=2) as r:row['multiplayer']=json.load(r)
 except OSError:row['unavailable']=True
 print(json.dumps(row),flush=True);time.sleep(5)
"""
    commands["telemetry"] = [
        *SSH,
        HOST,
        "timeout --kill-after=2 315 python3 -u -c " + shlex.quote(telemetry),
    ]
    arrival_started = []

    def run(item):
        name, command = item
        with (output / f"{name}.log").open("w") as stream:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                stream.write(line)
                stream.flush()
                if name == "arrival":
                    try:
                        if json.loads(line).get("stage") == "traversing":
                            arrival_started.append(time.time())
                    except ValueError:
                        pass
            return name, proc.wait()

    # The parent owns the deadline; all children inherit its process group.
    with ThreadPoolExecutor(max_workers=4) as pool:
        exits = dict(pool.map(run, commands.items()))
    (output / "drivers.json").write_text(remote("cat " + remote_output) + "\n")
    after = snapshot()
    (output / "after.json").write_text(json.dumps(after, indent=2) + "\n")
    drivers = json.loads((output / "drivers.json").read_text())["summary"]
    sockets = json.loads((output / "sockets.json").read_text())
    arrival_path = (
        Path(os.environ["TURKU3D_WORK"]) / "reports/tile-arrival/lan-warmed-50/summary.json"
    )
    arrival = json.loads(arrival_path.read_text()) if arrival_path.exists() else dict(passed=False)
    samples = [
        json.loads(line)
        for line in (output / "telemetry.log").read_text().splitlines()
        if line.startswith("{")
    ]
    overlap = (
        max(
            0,
            min(sockets["ended_at"], arrival_started[0] + 120)
            - max(sockets["started_at"], arrival_started[0]),
        )
        if arrival_started
        else 0
    )
    rejected = (
        after["multiplayer"]["stats"]["rejected"] - before["multiplayer"]["stats"]["rejected"]
    )
    accepted = after["multiplayer"]["stats"]["poses"] - before["multiplayer"]["stats"]["poses"]
    cpu_start = [int(v) for v in before["cpu"].split()[1:9]]
    cpu_end = [int(v) for v in after["cpu"].split()[1:9]]
    ticks = sum(cpu_end) - sum(cpu_start)
    idle = sum(cpu_end[3:5]) - sum(cpu_start[3:5])
    health_alerts = smoke_alerts(after["health"])
    warmed = all(
        before["coverage"][phase]["cached"] + before["coverage"][phase]["unavailable"]
        == before["coverage"][phase]["total"]
        for phase in ("spawn", "routes")
    )
    result = dict(
        commit=commit,
        same_generator=commit == after["commit"] and before["code"] == after["code"],
        warmed=warmed,
        coverage=before["coverage"],
        exits=exits,
        drivers=drivers,
        wider_warmed=before["coverage"]["wider"]["cached"]
        + before["coverage"]["wider"]["unavailable"]
        == before["coverage"]["wider"]["total"],
        sockets=sockets,
        arrival=arrival,
        rejected_delta=rejected,
        accepted_delta=accepted,
        sockets_and_render_overlap_seconds=round(overlap, 2),
        host_cpu_percent_of_four_cores=round(400 * (1 - idle / ticks), 2) if ticks else None,
        memory_available_min=min(
            [before["memory"]["MemAvailable"], after["memory"]["MemAvailable"]]
            + [s["available"] for s in samples]
        ),
        telemetry_samples=len(samples),
        swap_used_peak=max(
            [before["memory"]["SwapUsed"], after["memory"]["SwapUsed"]]
            + [s["swap_used"] for s in samples]
        ),
        health_alerts=health_alerts,
        disk_free_after=after["disk_free"],
    )
    result["passed"] = (
        not any(exits.values())
        and drivers["passed"]
        and sockets["passed"]
        and arrival["passed"]
        and rejected == 0
        and accepted >= sockets["sent"] * 0.95
        and not health_alerts
        and result["same_generator"]
        and warmed
        and overlap >= 110
        and result["swap_used_peak"] <= 1024**3
        and after["disk_free"]["/"] >= 5 * 1024**3
        and after["disk_free"]["/srv/turku3d"] >= 15 * 1024**3
    )
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return int(not result["passed"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return run_test(args.output)
    # Timeout includes FIFO admission. Kill only this owned process group, locally
    # and on OCI (remote timeout is shorter than this outer deadline).
    command = [
        sys.executable,
        str(HERE / "host_slots.py"),
        "build",
        "--",
        sys.executable,
        str(HERE / "lan_test.py"),
        "--worker",
        "--output",
        str(args.output),
    ]
    proc = subprocess.Popen(command, start_new_session=True)
    try:
        return proc.wait(timeout=595)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "timeout.json").write_text(
            json.dumps(dict(passed=False, reason="600s wall budget, including slots")) + "\n"
        )
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
