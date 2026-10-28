"""Repeatable 50 km/h centre drive through the public OCI boundary (run on OCI for private telemetry)."""
import argparse
import concurrent.futures
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path


def get(url):
    try:
        req = urllib.request.Request(url, headers={"Origin": "https://wnt.github.io"})
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def run(args):
    start = time.monotonic()
    rows, samples = {}, []
    root = Path.home() / "turku3d-drafts/work/coarse"
    client = "drive-" + str(time.time_ns())
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        while (now := time.monotonic() - start) < args.seconds:
            x, z = args.x + now * (50 / 3.6), args.z
            ci, cj = math.floor(x / 250), math.floor(z / 250)
            wanted = [f"t0_{i}_{j}" for i in range(ci - 1, ci + 4) for j in range(cj - 1, cj + 2)]
            wanted.sort(key=lambda t: (int(t.split('_')[2]) != cj or int(t.split('_')[1]) < ci,
                                       abs(int(t.split('_')[1]) - ci), t))
            if args.demand:
                get(args.base + 'demand?client=' + client + '&tiles=' + ','.join(wanted) + '&urgent=' + ','.join(t for t in wanted if int(t.split('_')[2]) == cj and ci <= int(t.split('_')[1]) <= math.floor((x + 30 * 50 / 3.6) / 250)) + '&visible=' + ','.join(
                    t for t in wanted if abs(int(t.split('_')[1]) - ci) <= 1))
            jobs = {}
            for tile in wanted:
                row = rows.setdefault(tile, {"request": now, "first": None, "full": None, "arrival": None})
                if tile == f"t0_{ci}_{cj}" and row['arrival'] is None:
                    row['arrival'] = now
                jobs[tile] = pool.submit(get, args.base + tile + '/manifest.json' +
                                        ('?client=' + client if args.demand else ''))
            states = {}
            for tile, job in jobs.items():
                status, body = job.result()
                states[tile] = status
                row = rows[tile]
                if status != 200:
                    continue
                stamp = time.monotonic() - start
                try:
                    manifest = json.loads((root / tile / 'manifest.json').read_text())
                except (OSError, ValueError):
                    manifest = body
                if row['first'] is None:
                    row['first'] = stamp
                if manifest.get('pass_kind') == 'full' and not body.get('stale') and row['full'] is None:
                    row['full'] = stamp
            try:
                outstanding = json.loads((root / '.requests.json').read_text())
            except (OSError, ValueError):
                outstanding = {}
            try:
                scheduler = json.loads((root / '.scheduler.json').read_text()) if args.demand else {}
            except (OSError, ValueError):
                scheduler = {}
            samples.append({"t": now, "scheduler": scheduler, "wanted": wanted, "http": states, "journal_outstanding": len(outstanding),
                            "journal_outside_wanted": len(set(outstanding) - set(wanted))})
            args.output.write_text(json.dumps({"seconds": now, "started_utc": time.time() - (time.monotonic() - start), "tiles": rows, "samples": samples}, indent=2) + '\n')
            time.sleep(max(0, 1 - (time.monotonic() - start - now)))
    crossings = [r for r in rows.values() if r['arrival'] is not None and r['arrival'] > 1]
    print(json.dumps({"crossings": len(crossings), "first_before_arrival": sum(r['first'] is not None and r['first'] <= r['arrival'] for r in crossings),
                      "full_by_arrival_plus_5": sum(r['full'] is not None and r['full'] <= r['arrival'] + 5 for r in crossings),
                      "report": str(args.output)}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', default='https://turku.lab.madekivi.fi/coarse/')
    p.add_argument('--seconds', type=float, default=120)
    p.add_argument('--demand', action='store_true')
    p.add_argument('--x', type=float, default=-375)
    p.add_argument('--z', type=float, default=-375)
    p.add_argument('--output', type=Path, required=True)
    run(p.parse_args())
