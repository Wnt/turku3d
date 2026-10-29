# One owner for architectural wall surfaces

The renewed flicker has a different immediate trigger from `fix-facade-flicker`:
reviewed facade programs drew material bands 0.5 mm above the base, excluded
regions at 1 mm, trim/surrounds at 2 mm, openings at 3 mm and balcony rails at
4 mm. These patches overlapped the base and each other; two same-priority
patches could be exactly coplanar. The earlier LoD2 cleanup ran before these
patches existed and separately for each passage-split shell.

Additional reproducible routes were found: overlapping passage parts and
neighbouring building records, shared frontage crossing a visual-tile boundary,
portal jamb/header intersections, and slightly diverging wall planes that enter
the near-coplanar band over only part of a triangle. Roof/wall edges without
positive overlapping area are seams, not depth competitors. Back-to-back faces
are retained because the materials use backface culling.

## Construction and invariant

* Reviewed paint is a partition in wall metres. Higher-priority openings, rails,
  trim and material regions cut their coverage out of lower-priority paint;
  all resulting pieces share one plane without overlapping area. UVs and
  opening origins remain in the original wall's coordinates.
* Reviewed programs own their observed segments. Existing stored maps retain
  unobserved remainders; descriptions and rule layouts remain the lower-priority
  fallback. No source descriptions, maps, reviewed programs or texture-library
  assets are deleted or regenerated.
* One assembly sees all LoD2 parts, footprint walls and passage splits, plus
  neighbouring complete-building context. Stable source ordering makes ownership
  independent of tile build order. All disconnected parts of a source building
  belong to the half-open tile containing its largest footprint part's centroid;
  passage cuts inherit that owner. Neighbour context is never emitted.
* Secondary architectural surfaces (including tunnels, portals, bridges and
  fences) subtract facade coverage and preceding structural coverage, preserving
  exposed triangles, material metadata and interpolated UVs. Portal jambs also
  stop at the sloping header rather than overlapping its vertical span.
* Every first/full build validates architectural wall triangles at exported
  float32 precision before publishing its visual pointer. The invariant rejects
  same-facing faces with dot product above 0.99999, separation at most 5 mm and
  positive overlap above 0.0001 m². A 50 µm inset excludes shared-edge rounding.
  Assembly uses 6 mm and a small angular margin. Errors identify tile,
  building/object and face IDs. Instanced vegetation and props are not
  architectural wall surfaces.
* Boundary admission serializes neighbour evidence under the producer's work
  root, detects duplicate building ownership and checks shared wall coverage.
  Concurrent builds cannot both admit conflicting boundary geometry.
* First-pass building visuals now use the same wall assembly instead of drawing
  overlapping closed collision solids. Collision remains structural. The client
  detaches the old tile before attaching its replacement, with an event-time
  regression proving the scene never contains both passes.

## Measured scope

Read-only reconstruction from the exact `tunnels` stage keys recorded in the
four cached core-tile stage reports, with existing reviewed facade publications.
The baseline uses the pre-change wall painter/assembly. Counts use exported
float32 architectural triangles, including tunnel wall meshes; they are not a
claim about every tile on OCI or a visual assessment of Jonni's recording.

| Tile | Violating building IDs before | Tunnel wall objects before | Overlapping triangle pairs before | After |
| --- | ---: | ---: | ---: | ---: |
| t0_0_0 | 22 | 13 | 8,166 | 0 |
| t0_0_1 | 4 | 0 | 1,732 | 0 |
| t0_1_0 | 7 | 2 | 2,673 | 0 |
| t0_1_1 | 4 | 0 | 906 | 0 |
| Total | 37 | 15 | 13,477 | 0 |

The combined after-mesh also has zero violations across all four tile boundaries.
This includes Blanko (`103277347A`), Linnankatu 13 (`103304667L`) and the two
Linnankatu 11 records (`1032865783`, `1032865794`). The boundary audit specifically
caught shared frontage between `1032959104` and `103295904X`.
An additional selective replay of current tunnel generation and wall assembly
from cached building/terrain inputs also passed all four tiles and neighbour
admission. No area build, screenshot, browser or drive check was performed.
Private evidence is under the agent's `work/reports/walls/` directory.

## Verification and delivery

```sh
source ../sandbox.env
PYTHONPATH=pipeline:textures PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_wall_surfaces.py pipeline/tests/test_facade_wall_overlap.py pipeline/tests/test_facade_programs.py pipeline/tests/test_coarse_tunnels.py
PYTHONPATH=pipeline:textures PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse.py -k 'basic_visual or multipart_building'
npm --prefix game test -- tests/unit/coarse-tiles.test.ts
python3 scripts/dev/tests/run.py test_delivery.py
```

Delivery uses `codex-task finish`: Pages package/upload, OCI source deployment,
five-second curl smoke and a selective asynchronous test run. World tiles remain
on-demand. Content-addressed input/stage caches reuse unaffected data and assets;
there is no full-region rebuild. New wall helper paths also trigger OCI source
deployment when changed independently in a later slice.

The first post-deploy run passed 104 tests and exposed one dependency-classification
failure: wall helpers had been added to the facade-only source list. The forward
fix registers those helpers as structural producer dependencies and hashes only
ownership functions from `stored_facades.py` for first-pass freshness. A regression
checks that stored-map binding edits preserve structural identity while ownership
and invariant edits invalidate it. The original cache-separation assertion remains.
