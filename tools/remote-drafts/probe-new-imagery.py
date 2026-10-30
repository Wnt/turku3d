"""Selective deployed first/full build with 2025 imagery; existing world tiles stay untouched."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.build import build
from turku3d.coarse.collision_key import code_hash as structural_code
from turku3d.coarse.imagery import ImagerySources, blank, fetch, full_exists
from turku3d.coarse.packaging import current_code
from turku3d.coarse.retention import references
from turku3d.coarse.sources import NoCoverage, Sources, block_of, block_tiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    store = C.work_root() / "coarse"
    source = Sources()
    plan = json.loads(args.plan.read_text())
    assert current_code() == plan["generator"]
    assert structural_code() == plan["structural"]
    tile = None
    for phase in plan["phases"]:
        for name in phase["tiles"]:
            candidate = C.VisualTile.parse(name)
            if full_exists(candidate, store):
                continue
            try:
                path = source.ortho_source(candidate)
                if path and blank(candidate, path) and not source.missing_for_service(candidate):
                    tile = candidate
                    break
            except NoCoverage:
                continue
        if tile:
            break
    assert tile is not None, "No unpublished, input-ready blank-imagery candidate"
    protected = [t for t in block_tiles(block_of(tile)) if full_exists(t, store)]
    screenshot = C.VisualTile.parse("t0_0_37")
    if full_exists(screenshot, store) and screenshot not in protected:
        protected.append(screenshot)
    before = {
        t.id: (source.input_key(t), (store / t.id / "manifest.json").read_bytes())
        for t in protected
    }
    fetch(block_of(tile), root=store)
    sources = ImagerySources(store)
    path = sources.ortho_source(tile)
    assert path and not blank(tile, path)
    summary = json.loads(path.with_suffix(".summary.json").read_text())
    assert summary["layer"] == "Ilmakuva 2025"
    passes = {}
    for first in (True, False):
        start = time.monotonic()
        m = build(tile.id, args.out / tile.id, sources=sources, first_pass=first)
        assert m["inputs"] == sources.input_key(tile, first_pass=first)
        assert len(m["collision"]) == 16
        for rel in references(m):
            payload = args.out / tile.id / rel
            assert hashlib.sha256(payload.read_bytes()).hexdigest() == payload.name.split(".")[-2]
        passes[m["pass_kind"]] = dict(
            seconds=round(time.monotonic() - start, 3),
            buildings=m["buildings"],
            collision=len(m["collision"]),
        )
    source = Sources()
    for t in protected:
        assert source.input_key(t) == before[t.id][0], t.id
        assert (store / t.id / "manifest.json").read_bytes() == before[t.id][1], t.id
    result = dict(
        tile=tile.id,
        imagery=summary["layer"],
        passes=passes,
        preserved=[t.id for t in protected],
        generator=current_code(),
        structural=structural_code(),
        seconds=round(time.monotonic() - started, 3),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
