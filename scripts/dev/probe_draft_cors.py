"""Exercise deployed draft CORS failures and ten drivers within the async job budget."""
import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from facade_publish import remote
from deploy_drafts import HOME


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", required=True)
    args = ap.parse_args()
    output = f"{HOME}/reports/cors-{args.commit[:12]}-{uuid.uuid4().hex[:8]}"
    source = f"{HOME}/sources/{args.commit}"
    port = int(os.environ["TURKU3D_PORT_BASE"]) + 3
    try:
        print(remote(f"TURKU3D_DRAFT_SOURCE={source} {HOME}/run.sh timeout 480s python "
                     f"{source}/tools/remote-drafts/probe-cors.py --port {port} --output {shlex.quote(output)}"), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        print(error.stderr, file=sys.stderr, flush=True)
        raise
    report = Path(os.environ["TURKU3D_WORK"]) / "reports/cors-deployed.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"remote_output": output, "ten_drivers": json.loads(remote(f"cat {output}/ten-drivers.json")),
                                 "failure_paths": json.loads(remote(f"cat {output}/failure-paths.json"))}, indent=2))
    print(str(report), flush=True)


if __name__ == "__main__":
    main()
