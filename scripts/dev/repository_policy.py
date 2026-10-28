"""Uncached repository-wide safety stages required for every delivery selection."""

import concurrent.futures
import tomllib
from pathlib import Path

from gate_lib import environment, execute, inventory


def main():
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "scripts/dev/gate.toml").read_text())
    names = ("policy", "chrome-launch-policy")
    stages = {stage["name"]: stage for stage in config["stages"]}
    files, env = inventory(root), environment(root, config)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = [
            pool.submit(execute, root, stages[name], [], files, True, env, config) for name in names
        ]
        results = [future.result() for future in futures]
    for result in results:
        print(f"{result['status']} {result['name']} {result['seconds']:.3f}s", flush=True)
        print(result["output"], flush=True)
    return int(any(result["status"] != "PASS" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
