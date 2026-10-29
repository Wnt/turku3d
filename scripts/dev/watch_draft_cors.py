"""Record curl CORS coverage continuously across a real producer deployment."""
import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path


def probe(pair):
    method, path = pair
    result = subprocess.run(["curl", "-sS", "--max-time", "30", "-X", method,
                             "-H", "Origin: https://wnt.github.io", "-D", "-",
                             "https://turku.lab.madekivi.fi/coarse/" + path], capture_output=True, text=True)
    headers, _, body = result.stdout.partition("\r\n\r\n")
    if not body:  # text mode normalizes CRLF
        headers, _, body = result.stdout.partition("\n\n")
    status = next((int(line.split()[1]) for line in headers.splitlines() if line.startswith("HTTP/")), 0)
    try:
        instance = json.loads(body).get("instance")
    except (ValueError, AttributeError):
        instance = None
    return dict(at=time.time(), method=method, path=path, status=status, exit=result.returncode,
                cors=headers.lower().count("access-control-allow-origin:") == 1
                and "access-control-allow-origin: https://wnt.github.io" in headers.lower(), instance=instance)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    rows, instances = [], []
    started = time.monotonic()
    pairs = [("GET", "health"), ("GET", "missing"), ("GET", "demand?client=x"),
             ("OPTIONS", "health"), ("PUT", "health")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        while time.monotonic() - started < args.seconds:
            batch = list(pool.map(probe, pairs))
            rows.extend(batch)
            for row in batch:
                if row["instance"] and (not instances or instances[-1]["instance"] != row["instance"]):
                    instances.append(dict(at=row["at"], instance=row["instance"]))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(dict(instances=instances, rows=rows)))
            time.sleep(.3)
    print(json.dumps({"responses": len(rows), "non_cors": sum(r["status"] > 0 and not r["cors"] for r in rows),
                      "transport_errors": sum(bool(r["exit"]) for r in rows), "instances": instances}), flush=True)


if __name__ == "__main__":
    main()
