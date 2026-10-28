"""Ten independent route-hinted drivers against real HTTP drafts, with OCI telemetry.

Uses actual directed city roads, 30 s hints and the game's 3x3 visible ring.
Startup waits for all ten current tiles; that delay is reported separately.
Collision readiness includes downloading and SHA-256 checking all sixteen cells.
Run through host_slots.py with a hard ten-minute external timeout.
"""

import argparse
import concurrent.futures
import hashlib
import heapq
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def get(base, path, binary=False, timeout=5):
    try:
        req = urllib.request.Request(base + path, headers={"Origin": "https://wnt.github.io"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read() if binary else json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (OSError, ValueError):
        return 0, {}


def routes(graph):
    from turku3d.content.addresses import Graph
    from turku3d.roads import CAR, ONEWAY

    decoded = Graph(graph)
    nodes = list(zip(graph["nodes"][::2], graph["nodes"][1::2], strict=True))
    adjacency = {}
    for edge in decoded.edges:
        if not edge["flags"] & CAR:
            continue
        a, b, points = edge["a"], edge["b"], edge["points"]
        adjacency.setdefault(a, []).append((b, edge["cum"][-1], points))
        if not edge["flags"] & ONEWAY:
            adjacency.setdefault(b, []).append((a, edge["cum"][-1], points[::-1]))
    result = []
    for k in range(10):
        angle = 2 * math.pi * k / 10
        direction = math.cos(angle), math.sin(angle)
        start = min(
            adjacency, key=lambda n: math.dist(nodes[n], (direction[0] * 600, direction[1] * 600))
        )
        target = (direction[0] * 3000, direction[1] * 3000)
        distances, previous, queue = {start: 0}, {}, [(0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            for neighbour, length, points in adjacency.get(node, []):
                candidate = distance + length
                if candidate < distances.get(neighbour, math.inf):
                    distances[neighbour] = candidate
                    previous[neighbour] = node, points
                    heapq.heappush(queue, (candidate, neighbour))
        goal = min(distances, key=lambda n: math.dist(nodes[n], target))
        parts = []
        while goal != start:
            goal, points = previous[goal]
            parts.append(points)
        points = [p for part in parts[::-1] for p in part[:-1]] + [parts[0][-1]]
        segments, length = [], 0
        for a, b in zip(points[:-1], points[1:], strict=True):
            size = math.dist(a, b)
            if size:
                segments.append((length, length + size, a, b))
                length += size
        result.append(dict(segments=segments, length=length))
    return result


def point(route, distance):
    for start, end, a, b in route["segments"]:
        if distance <= end:
            t = max(0, (distance - start) / (end - start))
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    return route["segments"][-1][3]


def cell(x, z):
    return f"t0_{math.floor(x / 250)}_{math.floor(z / 250)}"


def crossings(route):
    out = []
    for start, end, a, b in route["segments"]:
        cuts = [0.0, 1.0]
        for axis in (0, 1):
            if a[axis] == b[axis]:
                continue
            for edge in range(
                math.floor(min(a[axis], b[axis]) / 250) + 1, math.ceil(max(a[axis], b[axis]) / 250)
            ):
                cuts.append((edge * 250 - a[axis]) / (b[axis] - a[axis]))
        cuts.sort()
        for lo, hi in zip(cuts[:-1], cuts[1:], strict=True):
            t = (lo + hi) / 2
            tile = cell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if not out or out[-1][1] != tile:
                out.append((start + lo * (end - start), tile))
    return out


def ring(tile):
    _, x, z = tile.split("_")
    return [
        f"t0_{i}_{j}" for i in range(int(x) - 1, int(x) + 2) for j in range(int(z) - 1, int(z) + 2)
    ]


def cpu():
    values = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
    return sum(values), sum(values[3:5])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:7764/coarse/")
    ap.add_argument("--root", type=Path, default=Path("/srv/turku3d/work/coarse"))
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--speed-kmh", type=float, default=120)
    args = ap.parse_args()
    status, navigation = get(args.base, "navigation.json", timeout=60)
    if status != 200:
        raise RuntimeError(f"City road graph unavailable: HTTP {status}")
    paths = routes(navigation["roads"])
    for path in paths:
        path["crossings"] = crossings(path)
    speed = args.speed_kmh / 3.6
    client_prefix = "ten-drivers-" + str(time.time_ns())
    started, moving = time.monotonic(), None
    initial_cpu, samples, ready, pending, first_request, statuses = cpu(), [], {}, {}, {}, {}
    states, demands = {}, {}
    initial = {cell(*point(route, 0)) for route in paths}

    def fetch_collision(tile, client):
        status, manifest = get(args.base, tile + "/collision.json?client=" + client)
        if status != 200:
            states[tile] = dict(at=time.monotonic() - started, status=status, **manifest)
            return status, None
        entries = manifest.get("collision", {})
        if len(entries) != 16:
            return 0, None
        for entry in entries.values():
            status, body = get(args.base, tile + "/" + entry["url"], binary=True)
            if (
                status != 200
                or len(body) != entry["bytes"]
                or hashlib.sha256(body).hexdigest() != entry["sha256"]
            ):
                return 0, None
        return 200, time.monotonic() - started

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        while time.monotonic() - started < 540:
            tick = time.monotonic()
            elapsed = tick - started
            travel = 0 if moving is None else min(speed * (tick - moving), speed * args.seconds)
            if moving is not None and tick - moving > args.seconds:
                break
            wanted_all = set()
            for i, route in enumerate(paths):
                here = cell(*point(route, travel))
                urgent = list(
                    dict.fromkeys(
                        [here]
                        + [
                            tile
                            for distance, tile in route["crossings"]
                            if travel < distance <= travel + speed * 30
                        ]
                    )
                )
                visible = ring(here)
                wanted = list(
                    dict.fromkeys(urgent + visible + [t for p in urgent for t in ring(p)])
                )[:128]
                wanted_all.update(wanted)
                client = client_prefix + "-" + str(i)
                query = urllib.parse.urlencode(
                    dict(
                        client=client,
                        tiles=",".join(wanted),
                        urgent=",".join(urgent),
                        visible=",".join(visible),
                    )
                )
                demand_status, _ = get(args.base, "demand?" + query)
                demands[demand_status] = demands.get(demand_status, 0) + 1
                for tile in wanted:
                    first_request.setdefault(tile, elapsed)
                    if tile not in ready and tile not in pending:
                        pending[tile] = pool.submit(fetch_collision, tile, client)
                # Full visuals compete with first passes just as in the game.
                if here in ready:
                    pool.submit(get, args.base, here + "/manifest.json?client=" + client)
            for tile, future in list(pending.items()):
                if future.done():
                    status, at = future.result()
                    statuses[status] = statuses.get(status, 0) + 1
                    if at is not None:
                        ready[tile] = at
                    del pending[tile]
            if moving is None and initial <= ready.keys():
                moving = time.monotonic()
            if moving is None and elapsed > 180:
                break
            try:
                scheduler = json.loads((args.root / ".scheduler.json").read_text())
            except (OSError, ValueError):
                scheduler = {}
            samples.append(
                dict(
                    t=elapsed,
                    queued=scheduler.get("queued"),
                    pending=len(scheduler.get("pending", {})),
                    building=len(scheduler.get("building", [])),
                    upgrading=len(scheduler.get("upgrading", [])),
                    wanted=len(wanted_all),
                    ready=len(ready),
                    cpu=cpu(),
                )
            )
            time.sleep(max(0, 1 - (time.monotonic() - tick)))
        for i in range(10):
            get(args.base, "demand?client=" + client_prefix + "-" + str(i) + "&tiles=")
    startup = None if moving is None else moving - started
    rows = []
    for driver, route in enumerate(paths):
        for distance, tile in route["crossings"]:
            if startup is None or not 0 < distance / speed <= args.seconds:
                continue
            arrival = startup + distance / speed
            at = ready.get(tile)
            rows.append(
                dict(
                    driver=driver,
                    tile=tile,
                    arrival=arrival,
                    ready=at,
                    lateness=None if at is None else max(0, at - arrival),
                )
            )
    end_cpu = cpu()
    busy = (
        100 * os.cpu_count() * (1 - (end_cpu[1] - initial_cpu[1]) / (end_cpu[0] - initial_cpu[0]))
    )
    late = [r for r in rows if r["lateness"] is None or r["lateness"] > 0]
    summary = dict(
        drivers=10,
        speed_kmh=args.speed_kmh,
        drive_seconds=args.seconds,
        startup_seconds=startup,
        crossings=len(rows),
        late_crossings=len(late),
        missing=sum(r["ready"] is None for r in rows),
        worst_collision_lateness=max((r["lateness"] or 0 for r in rows), default=None),
        queue_peak=max((s["queued"] or 0 for s in samples), default=0),
        host_cpu_percent=busy,
        http=statuses,
        demand_http=demands,
        passed=startup is not None and bool(rows) and not late,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            dict(
                summary=summary,
                crossings=rows,
                samples=samples,
                first_request=first_request,
                last_unready=states,
                routes=paths,
            ),
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
