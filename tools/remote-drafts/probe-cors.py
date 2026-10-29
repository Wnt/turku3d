"""Isolated Caddy failure-path checks, then ten real concurrent draft clients."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def curl(url, method="GET"):
    result = subprocess.run(["curl", "-sS", "--max-time", "33", *(["--head"] if method == "HEAD" else ["-X", method]),
                             "-H", "Origin: https://wnt.github.io", "-D", "-", "-o", "/dev/null", url],
                            capture_output=True, text=True)
    headers = result.stdout.lower()
    status = next((int(line.split()[1]) for line in headers.splitlines() if line.startswith("http/")), 0)
    return {"method": method, "status": status, "cors": headers.count("access-control-allow-origin:") == 1
            and "access-control-allow-origin: https://wnt.github.io" in headers,
            "retry": "retry-after: 5" in headers, "exit": result.returncode}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    home = Path.home() / "turku3d-drafts"
    source = Path(__file__).resolve().parent
    config = (source / "Caddyfile").read_text().replace(":7764", f":{args.port}")
    upstream = (source / "draft-upstream.caddy").read_text().replace(":7765", f":{args.port + 1}")
    config = config.replace("import /home/ubuntu/turku3d-drafts/draft-upstream.caddy", upstream)
    target = args.output / "Caddyfile"
    target.write_text(config)
    rows = []
    with (args.output / "caddy.log").open("w") as log:
        proxy = subprocess.Popen([str(home / "tools/bin/caddy"), "run", "--config", str(target), "--adapter", "caddyfile"], stdout=log, stderr=log)
        try:
            time.sleep(.5)
            if proxy.poll() is not None:
                raise RuntimeError("isolated Caddy failed to start")
            base = f"http://127.0.0.1:{args.port}/coarse/"
            for method in ("GET", "HEAD", "OPTIONS"):
                row = curl(base + "health", method)
                row["case"] = "unavailable"
                rows.append(row)
                assert row["cors"] and row["status"] == (204 if method == "OPTIONS" else 502), row
                assert method == "OPTIONS" or row["retry"], row

            class Upstream(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path.endswith("timeout"):
                        time.sleep(31)
                    self.send_response(500)
                    self.end_headers()
                def log_message(self, *_):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", args.port + 1), Upstream)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for case in ("upstream-500", "timeout"):
                    row = curl(base + case)
                    row["case"] = case
                    rows.append(row)
                    assert row["cors"] and row["retry"] and row["status"] == (500 if case == "upstream-500" else 502), row
            finally:
                server.shutdown()
                server.server_close()
        finally:
            proxy.terminate()
            proxy.wait(timeout=5)
            (args.output / "failure-paths.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps({"failure_paths": rows}), flush=True)
    drivers = args.output / "ten-drivers.json"
    subprocess.run(["timeout", "420s", sys.executable, str(source / "ten-drivers.py"),
                    "--base", "https://turku.lab.madekivi.fi/coarse/", "--drivers", "10",
                    "--start-office", "--seconds", "30", "--output", str(drivers)], check=True)
    summary = json.loads(drivers.read_text())["summary"]
    assert summary["cors"]["responses"] > 0 and summary["cors"]["non_cors"] == 0, summary
    assert summary["cors"]["transport_errors"] == 0, summary


if __name__ == "__main__":
    main()
