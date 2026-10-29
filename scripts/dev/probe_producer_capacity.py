"""Run the native serving capacity acceptance on frozen published OCI source."""
import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip())
    parser.add_argument("--clients", type=int, choices=(1, 10), default=10)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    output = f"{HOME}/agent-work/producer-502s/{args.commit[:12]}-{args.clients}clients"
    command = ["timeout", "--kill-after=3", "550", f"{source}/tools/remote-drafts/run.sh",
               "python", f"{source}/tools/remote-drafts/probe-capacity.py",
               "--output", output, "--clients", str(args.clients)]
    try:
        print(remote("TURKU3D_DRAFT_SOURCE=" + shlex.quote(source) + " " + shlex.join(command)), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        print(error.stderr, file=sys.stderr, flush=True)
        raise
    finally:
        result = remote(f"cat {output}/result.json 2>/dev/null || echo null")
        path = Path(os.environ["TURKU3D_WORK"]) / "reports" / f"capacity-{args.clients}clients.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json.loads(result), indent=2) + "\n")


if __name__ == "__main__":
    main()
