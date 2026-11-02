#!/usr/bin/env python3
"""Count streamed resource responses per tile/minute from Playwright or capture JSONL.

Times are milliseconds (capture clock), or HH:MM:SS.sss (Playwright wall clock).
Select one continuous route window; response counts include HTTP cache hits.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def timestamp(value):
    if isinstance(value, str) and ':' in value:
        hour, minute, second = map(float, value.split(':'))
        return (hour * 3600 + minute * 60 + second) * 1000
    return float(value)


def resource(url):
    path = urlsplit(url).path
    if '/coarse/' not in path:
        return None
    # Generation URLs can change while the addressed payload stays identical.
    path = re.sub(r'/coarse/_v/[^/]+/', '/coarse/', path)
    tile = re.search(r'/(t\d+_-?\d+_-?\d+)/', path)
    tile = tile[1] if tile else None
    if path.endswith('/collision.json'):
        kind = 'collision_manifest'
    elif path.endswith('/manifest.json'):
        kind = 'manifest'
    elif re.search(r'/roads(?:\.[a-f0-9]{64})?\.json$', path):
        kind = 'roads'
    elif path.endswith('.col'):
        kind = 'collision_payload'
    elif '/scenery/' in path and path.endswith('.json'):
        kind = 'scenery_manifest'
    elif '/scenery/' in path and path.endswith('.glb'):
        kind = 'scenery_glb'
    elif path.endswith('.ktx2'):
        kind = 'texture'
    elif path.endswith('.glb'):
        kind = 'visual_glb'
    else:
        return None
    return kind, tile, path


def summarize(rows, start, end):
    if end <= start:
        raise ValueError('end must be after start')
    groups = defaultdict(Counter)
    statuses = defaultdict(Counter)
    for row in rows:
        if row.get('k', 'res') != 'res' or not start <= timestamp(row['t']) <= end:
            continue
        asset = resource(row.get('url', row.get('p', '')))
        if asset is None:
            continue
        kind, tile, path = asset
        groups[kind, tile][path] += 1
        statuses[kind, tile][str(row.get('status', row.get('s', 'unknown')))] += 1
    minutes = (end - start) / 60_000
    resources = []
    for (kind, tile), counts in sorted(groups.items(), key=lambda pair: (pair[0][0], pair[0][1] or '')):
        total = sum(counts.values())
        resources.append(dict(kind=kind, tile=tile, responses=total, per_minute=total / minutes,
                              unique_urls=len(counts), repeated_responses=total - len(counts),
                              max_per_url=max(counts.values()), statuses=dict(statuses[kind, tile])))
    return dict(start_ms=start, end_ms=end, seconds=(end - start) / 1000,
                resources=resources, interpretation='Responses include cache hits. URL identities remove query/fragment and normalize generation prefixes. Repeats alone do not establish overlapping residency or repeated decoding.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl', type=Path)
    parser.add_argument('--start', required=True, type=timestamp)
    parser.add_argument('--end', required=True, type=timestamp)
    args = parser.parse_args()
    try:
        with args.jsonl.open() as source:
            rows = (json.loads(line) for line in source if line.strip())
            print(json.dumps(summarize(rows, args.start, args.end), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
