# Quarter-area reference capture

The private Google 3D comparison tools support independent square extents inside
an L0 draft tile. The game tile grid and published layer contracts are unchanged.
Reference images, meshes, selection evidence and detailed findings remain local.

A private capture keeps its parent `tile` and adds `coverage_id` and
`extent_en: [emin, nmin, emax, nmax]` in the project's EPSG:3877 coordinates.
A 125 m quarter has its own metadata completeness audit, decoded mesh selection,
reference raster, matching own-build raster and coverage entry. Independent
orthophoto confirmation crops the same quarter before resampling; it must not
resize the whole parent tile into the quarter. Own-raster caches distinguish
extents belonging to the same parent.

Round 2 records seed `20261101` and selects 40 quarters across a 5 by 4 spatial
stratification of the requested box. It excludes existing captured parents and
quarters with at least 50% mapped water/forest, requires mapped buildings or an
urban road, and reserves examples containing bridge, tunnel, rail, park and quay
features. The reproducible plan and source-file inventory are private.

Two separate sandbox Chrome for Testing profiles each retain one Maps SPA tab.
A batch pans between quarters without reload recovery. Every rotation, zoom and
ordinary pan synchronizes the steering position from the effective native URL;
otherwise rotation/zoom translations accumulate position drift. A native URL is
a steering observation, not a calibrated N2000 camera pose. Captured metadata
independently proves whether the intended extent loaded. Leaving the normal 3D
map for a photo overlay fails that view. Bounded local pan retries retain partial
data without accepting it as a qualified reference.

Headed and headless initial captures of the same close 3D scene were visually
compared before choosing headless mode. Per-view timings, native positions,
loading queues, per-extent sessions and host browser accounting remain private.
Both concurrent browser runtimes count against the task's 120 browser-minute
allowance. An attempted quarter is not automatically a complete 0.015625 km²
reference.

OCI snapshots accept only full-quality, non-stale manifest responses. They freeze
the advertised GLB and referenced shared textures with SHA256 verification. Each
score retains the exact own-build hash and source/scorer identities; a later
producer deployment does not mutate that snapshot. Incomplete metadata,
unsupported calibration or unavailable semantic evidence causes an abstention.

The private findings summary ranks textures and bridges first, then ground,
wall and object geometry. Roof form, building height and roof colour remain
informational. Ground material appearance groups do not independently establish
a semantic material type. Manual type reviews record their independent imagery
and limits separately. Small props, wall geometry, bridge undersides and tunnel
interiors remain explicitly unmeasured where independent semantic scoring is
unavailable.

After the browser lanes close, an offline reconciliation can reuse original,
hash-identical response bodies observed by either browser. Each extent receives
another independent epoch/completeness check. This performs no new provider
requests and does not exempt missing nodes. Original inventories remain private.

An explicit round-2 review authorization also permits at most 5% missing required
terminal nodes when metadata is resolved and nonempty. The missing-node projected
footprints are excluded from both calibration and every score mask. The report
labels this partial qualification; empty captures and unresolved metadata remain
unmeasured. The round ends by scoring eligible extents with four processes and
handing off remaining capture work separately.

## Local tools and evidence

Tools: `~/turku3d/private/gmaps-extract/` (`round2_select.py`, `capture.mjs`,
`round2_oci.py`, `round2_process.py`, `round2_reconcile.py`,
`round2_summary.py`, `round2_map.py`).

Evidence root: `~/turku3d/private/gmaps-data/round2/`:

- `plan.json`: seed, selected extents, source inventory and exclusion evidence.
- `coverage.json`: per-quarter qualification, timing and score availability.
- `oci-inventory.json`: immutable own-build snapshots and accepted fetch times.
- `findings.md` and `findings.json`: ranked findings and explicit abstentions.
- `google-reference-coverage.png`: previous full tiles plus new quarter states.
- `headless-validation.json`: initial headed/headless rendering comparison.

From a sourced project sandbox, the focused regression checks are:

```sh
bash pipeline/env/run.sh python -m pytest -q -c /dev/null -p no:cacheprovider \
  /Users/wnt/turku3d/private/gmaps-extract/test_extent.py
```

The broader private suite is `test_private.py test_loop.py test_extent.py` in that
same directory. It covers extent isolation, own-cache separation and independent
orthophoto cropping alongside existing decoder, completeness and scoring cases.
No provider imagery or raw reference data is part of this document or the game.
