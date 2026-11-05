#!/usr/bin/env python3
"""Bulk host: one compact status block of the window run (window-status.sh on the Mac calls it; read-only, < 1 s).

Reads $G/window.json and window.started, /run/bulk-ttl, /run/window-samples (window-run.sh's 5 s samples: CPU
jiffies, tunnel bytes, FS-Cache reads served locally vs fetched), the pass's pass.json and journal, cities.json and
the cities' window-builds.jsonl, and the farm's events. Prints "no window" when no window run is active.
"""
import json
import os
from pathlib import Path
import time

G = Path('/home/wnt/turku3d-bulk-gen3')
F = Path('/home/wnt/turku3d-local/loops/tiles/farm')


def js(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def num(path):
    try:
        return int(Path(path).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def rows(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def ring(tile, centre='t0_0_0'):
    i, j = (int(v) for v in tile.split('_')[1:])
    ci, cj = (int(v) for v in centre.split('_')[1:])
    return max(abs(i - ci), abs(j - cj))


def main():
    now = time.time()
    started = num(G / 'window.started')
    w = js(G / 'window.json', {})
    if not started or not w:
        print('no window')
        return 0
    cores = os.cpu_count()
    ttl = js('/run/bulk-ttl', {})
    left = f"{(ttl['poweroff_at'] - now) / 60:.0f} min" if ttl.get('poweroff_at') else 'not armed (<32 cores)'
    print(f"running yes | host {os.uname().nodename} {cores} cores {os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30:.0f} GB"
          f" | {(now - started) / 60:.0f} min since start | TTL left {left}")
    samples = [line.split() for line in Path('/run/window-samples').read_text().splitlines()] \
        if Path('/run/window-samples').exists() else []
    if len(samples) >= 2:
        a = next((s for s in samples if now - int(s[0]) <= 65), samples[0])
        b = samples[-1]
        d = [int(y) - int(x) for x, y in zip(a[1:9], b[1:9])]
        total = sum(d) or 1
        dt = max(1, int(b[0]) - int(a[0]))
        rx = (int(b[9]) - int(a[9])) / dt / 1e6
        hits, fetched = int(b[10]) - int(a[10]), int(b[11]) - int(a[11])
        rate = f"{100 * hits / (hits + fetched):.0f}%" if hits + fetched else 'n/a'
        print(f"cpu {dt}s: busy {100 * (d[0] + d[1] + d[2] + d[5] + d[6]) / total:.0f}% "
              f"(user {100 * (d[0] + d[1]) / total:.0f} sys {100 * (d[2] + d[5] + d[6]) / total:.0f}) "
              f"idle {100 * d[3] / total:.0f}% iowait {100 * d[4] / total:.0f}% | load {open('/proc/loadavg').read().split()[0]}"
              f" | tunnel {rx:.1f} MB/s | cache hits {rate} ({hits} reads local, {fetched} fetched)")
    mem = dict((k, int(v.split()[0])) for k, v in (line.split(':') for line in open('/proc/meminfo')))
    print(f"ram used {(mem['MemTotal'] - mem['MemAvailable']) / 2**20:.0f} GB free {mem['MemAvailable'] / 2**20:.0f} GB"
          f" | swap used {(mem['SwapTotal'] - mem['SwapFree']) / 2**20:.1f} GB")
    meta = js(F / 'revisions' / w['revision'] / 'revision.json', {})
    log = F / 'migrations' / meta.get('generation', '-')
    p = js(log / 'pass.json', {})
    c = js(G / 'cities.json', {})
    print(f"lanes turku {len(p.get('running', []))}/{num(G / 'lanes-turku.effective')} (cap {p.get('cap')}, "
          f"{len(p.get('waiting_for_rebuild', []))} waiting rebuild) | cities {len(c.get('running', []))}/{num(G / 'lanes-cities')}"
          f" | farm builds {sum(1 for _ in Path('/proc').glob('[0-9]*') if _cmd(_).startswith('python -m turku3d.coarse build'))}")
    try:
        prio = len(Path(os.environ.get('QUEUE_FILE', G / 'queue-turku.txt')).read_text().split())
    except OSError:
        prio = 0
    print(f"queue turku {p.get('left')} left of the pass's set, priority list {prio} (queue-turku.txt) | cities left: "
          + ' '.join(f"{k} {v}" for k, v in (c.get('left') or {}).items())
          + f" of {sum(len(cc['tiles']) for cc in js(w.get('plan', G / 'cities-plan.json'), {'cities': []})['cities'])} planned")
    since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started))
    done = {}
    errors = []
    for r in rows(log / 'pass.jsonl')[num(G / 'window.journal0') or 0:]:
        st = r.get('state')
        if st in ('migrated', 'kept', 'failed'):
            k = ('turku', ring(r['tile']))
            done.setdefault(k, [0, 0])[st == 'failed'] += 1
            if st == 'failed':
                errors.append((r.get('at', ''), f"turku/{r['tile']}: {str(r.get('error'))[:160]}"))
    plan = js(w.get('plan', G / 'cities-plan.json'), {'cities': []})
    for city in plan['cities']:
        for r in rows(Path(city['out']).parent / 'window-builds.jsonl'):
            if r.get('at', '') < since:
                continue
            k = (city['name'], r.get('ring'))
            done.setdefault(k, [0, 0])[r['state'] == 'failed'] += 1
            if r['state'] == 'failed':
                errors.append((r['at'], f"{city['name']}/{r['tile']}: exit {r.get('exit')} {r.get('error', '')}"[:200]))
    for e in rows(F / 'events.jsonl')[-400:]:
        if e.get('event') == 'failed':
            errors.append((time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['t'])), f"farm/{e.get('tile')}: {str(e.get('error'))[:160]}"))
    by_city = {}
    for (city, r), (ok, bad) in sorted(done.items(), key=lambda kv: (kv[0][0], kv[0][1] if kv[0][1] is not None else -1)):
        by_city.setdefault(city, []).append(f"r{r} {ok}" + (f"/{bad}F" if bad else ''))
    print('done ' + (' | '.join(f"{city}: {' '.join(v)}" for city, v in by_city.items()) or 'none yet'))
    for at, text in sorted(errors)[-3:]:
        print(f"error {at[11:19]} {text}")
    host_ready = sum(1 for _ in (Path('/srv/farm/upper/products') / meta.get('generation', '-')).glob('*/ready.json'))
    city_built = sum(sum(1 for r in rows(Path(cc['out']).parent / 'window-builds.jsonl') if r.get('state') == 'built')
                     for cc in plan['cities'])
    print(f"built here: turku {host_ready} ready, cities {city_built}")
    return 0


def _cmd(proc):
    try:
        return (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        return ''


if __name__ == '__main__':
    raise SystemExit(main())
