# Region runner evidence

The W2-02 implementation and schema are documented in
[pipeline/turku3d/region/README.md](../../pipeline/turku3d/region/README.md).
The attached [next-ring estimate](next-ring-estimate.json) is an offline run against cached
work-3x3 inputs. It selects 12 visual / 192 collision tiles; nine LAZ tiles are already cached.
Deduplicated city footprint polygons give 162 buildings. Planning placeholders estimate
324 street facades, 108 panoramas, 660 model calls, 10,560,000 tokens, 21,150 seconds at four
workers, and 3.9 GB additional disk. No model calls were made. Weekly quota cost remains
unknown, so future spending adapters fail closed until calibrated estimates are available.

Synthetic tests cover polygon/tile selection, duplicate survey geometry filtering, resume
and invalidation, parallel per-tile logs, failed steps, quota refusal before subprocess
startup, atomic cache publication, deduplication, and safe partial-file GC. Texture tests
also prove two cache instances reuse a completed panorama without downloading it twice.
Existing W1-09 cache files were neither migrated nor deleted.

Run from the sandbox:

```sh
source ../sandbox.env
make region-test
make region AREA=next-ring DRY=1
make region AREA=office-block
make region AREA=office-block  # successful steps should now report cached
make cache-gc
```

Region logs and summaries are in `$TURKU3D_WORK/reports/region/`. The first gate run passed
pipeline, texture, lint, generated and policy stages; the existing tooling latency test
failed at 3.6065 seconds against its unchanged 2-second bound during concurrent host load.
The full run took 154.922 seconds. Final verification is recorded in the task result.

The real office-block run passed all four Rapier validators. A subsequent unchanged run
reported fetch, assemble and validate as cached (0.754 seconds summed step time); unavailable
producer layers were explicitly skipped. The next gate run was green in 73.554 seconds.

Original pre-rebase verification: all six selected gate stages passed in **13.615 seconds**, including
7 synthetic region tests and the legacy/shared panorama-cache regressions. `make cache-gc`
and `make cache-gc APPLY=1` succeeded; the new shared cache was empty and no data was removed.

## Batch-1 rebase verification

Rebased onto main `8c1f9a5`, including the second building-layer publication fix.
Resolved Python extras, texture documentation and panorama-cache conflicts while retaining
main's photosphere/Mapillary providers, read-through APIs and corrupt-JSON fallback. New
writes use the shared process lock and atomic publication; legacy caches remain read-only.
Mapillary images also reuse legacy caches. Shared `layers.py` now participates in region
fingerprints, and production assembly uses only the landed batch assembler.

Synced `.venv` with `pipeline[dev,fetch,lidar]`, `textures[post,dev]` and
`scripts/dev/requirements.txt`, plus the documented Street View dependencies and
`streetlevel==0.12.13 --no-deps`. The only changed Node lockfile was deleted with the old
texture encoder; no retained Node lockfile changed.

```sh
source ../sandbox.env
.venv/bin/python -m pytest -q -n 3 pipeline/tests/test_region.py textures/tests/test_paths.py textures/tests/test_facade_batch.py textures/tests/test_streetview_geometry.py textures/tests/test_mapillary_camera.py
make region AREA=next-ring DRY=1 REGION_WORKERS=3
```

Result: **43 passed in 1.56 seconds**, with three workers. Scoped Ruff lint and formatting
checks passed. The next-ring dry run succeeded. The full gate and real-data build were not
rerun during this rebase, as instructed; the orchestrator performs the landing gate.

## Rebase after gate-speed, lidar, props and vegetation landing

Rebased onto main `1626ead`. Kept the new `networkx` building dependency and vegetation
package data alongside region extras, and retained both focused-gate guidance and shared-cache
documentation. Synced Python dependencies including buildings and region extras; no Node
lockfiles changed. The same focused command above passed **43 tests in 5.15 seconds** with
three workers. Scoped Ruff lint/format and the next-ring dry run also passed. No full gate,
browser tests or real-data build ran; the orchestrator owns the landing gate.
