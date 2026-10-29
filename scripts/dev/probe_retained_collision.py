"""Curl a real retained OCI tile through its on-demand visual/collision upgrade."""

import argparse
import concurrent.futures
import hashlib
import json
import shlex
import subprocess
import time

from deploy_drafts import HOME
from facade_publish import remote

URL = "https://turku.lab.madekivi.fi/coarse/"
INVENTORY = r'''
import json
from pathlib import Path
from turku3d.coarse.packaging import current_code
from turku3d.coarse.serving import complete
root=Path('/srv/turku3d/work/coarse')
code=current_code()
paths=sorted(root.glob('t0_*_*/manifest.json'),key=lambda p:(p.parent.name!='t0_-2_1',p.parent.name))
for path in paths:
    try:
        value=json.loads(path.read_text())
        if value.get('code')!=code and value.get('pass_kind')=='full' and complete(root,path.parent.name,value):
            print(json.dumps(dict(current=code,retained=value)))
            break
    except (OSError,ValueError):
        continue
else:
    raise SystemExit('No complete outdated full tile available for the retained regression')
'''
POINTERS = r'''
import json
from pathlib import Path
root=Path('/srv/turku3d/work/coarse')/TILE
result={}
for name in ('manifest','collision'):
    try:
        result[name]=json.loads((root/(name+'.json')).read_text())
    except (OSError,ValueError):
        result[name]={}
print(json.dumps(result))
'''


def source_read(source, script):
    command = shlex.join([f"{source}/tools/remote-drafts/run.sh", "python", "-c", script])
    return json.loads(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command))


def curl(tile, endpoint):
    body = subprocess.check_output(
        ["curl", "--fail", "--silent", "--show-error", "--max-time", "12",
         "-H", "Origin: https://wnt.github.io", f"{URL}{tile}/{endpoint}.json"],
        text=True, timeout=15,
    )
    value = json.loads(body)
    assert value.get("coarse") and len(value.get("collision", {})) == 16, value
    assert not value.get("collision_pending"), value
    return value


def assert_pair(value, expected):
    assert value["generation"] == expected["code"]
    assert value["visual"] == expected["visual"]
    assert value["collision"] == expected["collision"]
    assert value.get("roads") == expected.get("roads")


def check_payloads(tile, fragment):
    """Verify the bytes the client would load, including each cell in the bundle."""
    entries = [fragment["visual"][tile], fragment["collision_bundle"]]
    bodies = []
    for entry in entries:
        body = subprocess.check_output(
            ["curl", "--fail", "--silent", "--show-error", "--max-time", "20",
             f"{URL}{tile}/{entry['url']}"], timeout=23,
        )
        assert len(body) == entry["bytes"]
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        bodies.append(body)
    offset = 0
    for _, entry in sorted(fragment["collision"].items()):
        cell = bodies[1][offset:offset + entry["bytes"]]
        assert hashlib.sha256(cell).hexdigest() == entry["sha256"]
        offset += entry["bytes"]
    assert offset == len(bodies[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    assert remote(f"readlink -f {HOME}/repo") == source, "Published OCI source moved before probe"
    inventory = source_read(source, INVENTORY)
    previous, current = inventory["retained"], inventory["current"]
    tile = previous["tile"]
    started = time.monotonic()
    stale_polls = 0
    first_observed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        while time.monotonic() - started < 150:
            responses = list(pool.map(lambda endpoint: curl(tile, endpoint), ("collision", "manifest")))
            pointers = source_read(source, POINTERS.replace("TILE", repr(tile)))
            for value in responses:
                if value.get("stale"):
                    assert_pair(value, previous)
                    if not stale_polls:
                        check_payloads(tile, value)
                    stale_polls += 1
                else:
                    assert_pair(value, pointers["manifest"])
                    assert value["generation"] == current
            first = pointers["collision"]
            if first.get("pass_kind") == "first" and first.get("code") == current:
                first_observed = True
                # Current first-pass physics must not attach under retained full visuals.
                for value in responses:
                    assert value.get("stale")
                    assert_pair(value, previous)
            if all(not value.get("stale") for value in responses):
                assert stale_polls, "Did not observe last-good collision during regeneration"
                check_payloads(tile, responses[0])
                print(json.dumps(dict(tile=tile,previous=previous["code"],current=current,
                                      stale_endpoint_polls=stale_polls,first_pass_observed=first_observed,
                                      collision_cells=16,paired_full_upgrade=True,
                                      seconds=round(time.monotonic()-started,3))),flush=True)
                return
            time.sleep(1)
    raise SystemExit(f"{tile}: paired current full did not arrive in 150 seconds ({stale_polls} retained polls)")


if __name__ == "__main__":
    main()
