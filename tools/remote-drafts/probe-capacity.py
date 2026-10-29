"""Native OCI serving acceptance using the forwarder's exact ten-player load tool."""
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def health(base):
    with urllib.request.urlopen(base + "/coarse/health", timeout=5) as reply:
        return json.load(reply)


def listen_drops():
    rows = Path("/proc/net/netstat").read_text().splitlines()
    for names, values in zip(rows[::2], rows[1::2], strict=False):
        if names.startswith("TcpExt:"):
            counters = dict(zip(names.split()[1:], map(int, values.split()[1:]), strict=False))
            return {key: counters[key] for key in ("ListenDrops", "ListenOverflows")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:7764")
    parser.add_argument("--clients", type=int, choices=(1, 10), default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--load-tool", type=Path, default=Path.home() /
                        "turku3d-drafts/agent-work/forwarder-capacity/forwarder-tunnel-load-arm64")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    before = health(args.base)
    # A just-activated slot may still be selecting its next job. Observe real
    # build contention instead of accidentally benchmarking that idle interval.
    while not before["active_builds"] and time.monotonic() - start < 30:
        time.sleep(1)
        before = health(args.base)
    counters = listen_drops()
    with urllib.request.urlopen(args.base + "/coarse/t0_0_0/manifest.json", timeout=5) as reply:
        manifest = json.load(reply)
    assets = ["/coarse/t0_0_0/" + entry["url"] for group in ("visual", "collision")
              for entry in manifest[group].values()]
    assert len(assets) == 17, "Acceptance needs a cached GLB and all sixteen collision cells"
    paths = dict(cached=assets, mixed=["/coarse/health", "/coarse/t0_0_0/manifest.json",
                                    "/coarse/not-a-tile/manifest.json", *assets])
    results, failures = [], []
    for repeat in range(args.repeats):
        for kind, entries in paths.items():
            path = args.output / f"{kind}-paths.txt"
            path.write_text("\n".join(entries) + "\n")
            command = [str(args.load_tool), "-base", args.base,
                       "-ws", args.base.replace("http", "ws", 1) + "/",
                       "-paths", str(path), "-clients", str(args.clients),
                       "-parallel", "32" if args.clients == 10 else "1",
                       "-rounds", "3", "-timeout", "5s"]
            run = subprocess.run(command, capture_output=True, text=True,
                                 timeout=max(1, min(90, 540 - (time.monotonic() - start))))
            (args.output / f"{kind}-{repeat}.json").write_text(run.stdout)
            row = json.loads(run.stdout)
            results.append(dict(kind=kind, repeat=repeat, **row))
            if run.returncode or row["transport_failures"] or row["headerless"] or row["errors"]:
                failures.append(f"{kind}-{repeat}: HTTP/transport/body/header failure")
            statuses = {int(key): value for key, value in row["statuses"].items()}
            if any(status >= 500 for status in statuses):
                failures.append(f"{kind}-{repeat}: 5xx")
            if kind == "cached" and (set(statuses) != {200} or row["p95_ms"] >= 1500):
                failures.append(f"{kind}-{repeat}: cached asset status/p95")
            if kind == "mixed" and statuses.get(404) != args.clients * 3:
                failures.append(f"{kind}-{repeat}: missing-path status")
            if row["ws_healthy_at_end"] != args.clients:
                failures.append(f"{kind}-{repeat}: multiplayer socket failure")
    after = health(args.base)
    drops = {key: value - counters[key] for key, value in listen_drops().items()}
    if any(drops.values()):
        failures.append("OCI listen drops increased (host-wide counters)")
    if not (before["active_builds"] or after["active_builds"]):
        failures.append("No concurrent producer build observed")
    report = dict(before=before, after=after, listen_delta=drops, runs=results,
                  failures=failures, seconds=round(time.monotonic() - start, 3))
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
