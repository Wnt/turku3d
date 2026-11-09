#!/usr/bin/env python3
"""City.lab smoke after a deploy (AGENTS.md flow step 3): curl-level, no browser, 5 s total, health-aware.

  python3 tools/share/smoke.py [SHA]     # exit 0 green, 1 red; one line per check

Checks, all in parallel under one 5 s deadline:
* version: city.lab /version.json names SHA (when given) and the page answers 200;
* tiles: a central manifest per city through city.lab is a full, non-stale pass;
* farm: the newest Turku train alias the share follows reports no build failures and nothing stuck building;
* cities: each city tile service on osgallery-dev (one ssh) reports failure_count 0;
* scenery: the distant scenery service (local-horizon, through city.lab) answers health in time, ready, with no
  failed request in the last 10 minutes (a hung service once froze every scenery request).
HTTP 200 alone is never green: each check reads the payload."""
import json, re, subprocess, sys, threading, time, urllib.request

SITE, FARM = "https://city.lab.madekivi.fi", "http://192.168.1.114:7950"
CITIES = {"espoo": 7970, "amsterdam": 7980, "rotterdam": 7990}
DEADLINE = time.monotonic() + 5.0
results = {}


def get(url):
    with urllib.request.urlopen(url, timeout=max(0.2, DEADLINE - time.monotonic())) as r:
        return r.status, r.read()


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # a timeout or refusal is red, never skipped
        ok, detail = False, f"{type(e).__name__}: {e}"
    results[name] = (ok, detail)


def version():
    status, page = get(f"{SITE}/")
    sha = json.loads(get(f"{SITE}/version.json")[1])["sha"]
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    return status == 200 and sha.startswith(want), f"page {status}, deployed {sha[:12]}" + (f", want {want}" if want else "")


def manifest(prefix):
    def run():
        m = json.loads(get(f"{SITE}/{prefix}coarse/t0_0_0/manifest.json")[1])
        return m.get("pass_kind") == "full" and not m.get("stale"), f"t0_0_0 {m.get('pass_kind')} stale={m.get('stale')}"
    return run


def farm():
    status = json.loads(get(f"{FARM}/status")[1])
    trains = sorted((int(m.group(1)), k) for k in status.get("aliases", {}) if (m := re.fullmatch(r"train(\d+)", k)))
    alias = trains[-1][1]
    h = json.loads(get(f"{FARM}/v/{alias}/coarse/health")[1])
    return h.get("state") == "ready" and not h.get("failures"), f"{alias} {h.get('state')} failures={len(h.get('failures') or [])} building={len(h.get('building') or [])} queued={h.get('queued')}"


def cities():
    script = "; ".join(f"echo {c} $(curl -s -m 2 http://127.0.0.1:{p}/coarse/status | head -c 200000 | python3 -c "
                       "'import json,sys;s=json.load(sys.stdin);print(s.get(\"state\"),s.get(\"failure_count\"))')"
                       for c, p in CITIES.items())
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=2", "-o", "BatchMode=yes", "osgallery-dev", script], capture_output=True,
                         text=True, timeout=max(0.5, DEADLINE - time.monotonic())).stdout.split("\n")
    rows = [line.split() for line in out if line.strip()]
    ok = len(rows) == len(CITIES) and all(len(r) == 3 and r[2] == "0" for r in rows)
    return ok, ", ".join(" ".join(r) for r in rows) or "no answer"


def scenery():
    h = json.loads(get(f"{SITE}/coarse/scenery/health")[1])
    errors = h.get("errors") or {}
    recent = bool((errors.get("last") or {}).get("t")) and time.time() - errors["last"]["t"] < 600
    return h.get("state") == "ready" and not recent, (f"{h.get('state')} pending={(h.get('cache') or {}).get('pending_builds')} "
                                                     f"errors={errors.get('count')}" + (", one in the last 10 min" if recent else ""))


checks = {"version": version, "scenery": scenery, "tiles:turku": manifest(""), "farm": farm, "cities": cities,
          **{f"tiles:{c}": manifest(f"cities/{c}/") for c in CITIES}}
threads = [threading.Thread(target=check, args=(n, f), daemon=True) for n, f in checks.items()]
for t in threads:
    t.start()
for t in threads:
    t.join(max(0, DEADLINE - time.monotonic()))
red = 0
for name in checks:
    ok, detail = results.get(name, (False, "no answer within 5 s"))
    red += not ok
    print(f"{'ok ' if ok else 'RED'} {name}: {detail}")
sys.exit(1 if red else 0)
