"""Request new outer tiles on deployed OCI and verify retained warm manifests."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from facade_publish import remote

BASE = "https://turku.lab.madekivi.fi/coarse/"


def get(path):
    request = Request(BASE + path, headers={"Origin": "https://wnt.github.io"})
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--tile", action="append", required=True)
    args = parser.parse_args()
    client = "outer-coverage-" + uuid.uuid4().hex[:12]
    started = time.monotonic()
    observed, complete, errors = {}, {}, []
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            while time.monotonic() - started < 420 and len(complete) < len(args.tile):
                status, body = get("demand?" + urlencode(dict(client=client, tiles=",".join(args.tile),
                                                             urgent=",".join(args.tile), visible=",".join(args.tile))))
                if status != 200:
                    raise RuntimeError(f"demand: HTTP {status}: {body!r}")
                pending = [(tile, kind) for tile in args.tile if tile not in complete
                           for kind in ("collision", "manifest")]
                replies = list(pool.map(lambda pair: get(f"{pair[0]}/{pair[1]}.json?client={client}"), pending))
                for (tile, kind), (status, body) in zip(pending, replies):
                    if status == 202:
                        continue
                    if status != 200:
                        raise RuntimeError(f"{tile}/{kind}: HTTP {status}: {body!r}")
                    manifest = json.loads(body)
                    if manifest.get("stale"):
                        continue  # Retained first pass can remain available during full enrichment.
                    assert len(manifest["collision"]) == 16, tile
                    pass_kind = manifest.get("pass_kind")
                    observed.setdefault(tile, {}).setdefault(pass_kind, round(time.monotonic() - started, 3))
                    if pass_kind == "full":
                        complete[tile] = manifest
                time.sleep(1)
        if len(complete) != len(args.tile):
            raise TimeoutError(f"Missing full tiles: {set(args.tile) - complete.keys()}")
        for tile, manifest in complete.items():
            for entry in [*manifest["visual"].values(), *manifest["collision"].values()]:
                status, payload = get(f"{tile}/{entry['url']}")
                assert status == 200 and len(payload) == entry["bytes"], tile
                assert hashlib.sha256(payload).hexdigest() == entry["sha256"], tile
    except Exception as error:
        errors.append(str(error))
    finally:
        get("demand?" + urlencode(dict(client=client, tiles="", urgent="", visible="")))

    script = '''import hashlib,json,pathlib
from turku3d.coarse.packaging import code_hash
from turku3d.coarse.collision_key import code_hash as structural_hash
root=pathlib.Path('/srv/turku3d/work/coarse')
print(json.dumps(dict(code=code_hash(),structural_code=structural_hash(),
 manifests={p.parent.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('t0_*/manifest.json')})))'''
    after = json.loads(remote("~/turku3d-drafts/run.sh python -c " + shlex.quote(script)))
    before = json.loads(args.baseline.read_text())
    current = {tile: record for tile, record in before.items() if record["code"] == after["code"]}
    changed = [tile for tile, record in current.items() if after["manifests"].get(tile) != record["sha256"]]
    if not current or changed:
        errors.append(f"Warm current manifests changed: {changed}; baseline current count: {len(current)}")
    result = dict(seconds=round(time.monotonic() - started, 3), passes=observed,
                  full_tiles=list(complete), warm_current_checked=len(current), warm_changed=changed,
                  code=after["code"], structural_code=after["structural_code"], errors=errors)
    print(json.dumps(result), flush=True)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
