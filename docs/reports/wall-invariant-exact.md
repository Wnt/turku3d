# Wall invariant recovery

## Failure audit

The retained OCI `reports/service*.log` files contained 41 wall invariant reports
since `44a2e7c6`: 35 neighbour reports and six in-tile reports, affecting 17 tiles.
There were no wall ownership failures. The first logged failure followed that
commit (job timestamp 1793285451.8353164). This audits all retained log files;
it cannot recover failures removed by log retention.

The reports contain 247 sampled triangle pairs, including repeat attempts.
Error messages truncate their pair lists, so these are sample counts, not the
complete number of overlapping surfaces:

| Pair kind | Logged samples | Resolution |
| --- | ---: | --- |
| Tunnel/building | 65 | Cut the building around the full 0.4 m lining, with 6 mm export margin, before painting. Resolve residual same-facing overlaps in assembly. |
| Basic structural wall/building | 52 | Resolve structural triangles against buildings in the first pass; compare only matching passes across tiles. |
| Explicitly mixed first/full passes | 48 | Separate pass records; require matching pass and generator content hash. |
| Roof/building | 45 | First-pass roof/building meshes now participate in the same assembly instead of being protected wholesale. |
| Same-pass building/building | 37 | Shared-source assembly and neighbour subtraction remove competing same-facing surfaces. |

Opposite-facing party walls are explicitly exempt: backface culling prevents the
two solid boundaries from competing. The logs lack normals, so the number of
party walls cannot be inferred from their text. A dedicated regression covers
this exemption. Legacy records lack version identity and are ignored, rather
than guessed comparable. The new records carry `pass_kind` and `content_hash`
(the generator's SHA-256 content identity), with separate first/full directories.

## Central evidence replay

Fetched existing boundary records from OCI into this sandbox, without rebuilding
any tile. Twelve retained records were available for the selected central area;
four requested records were absent. Their 10,676 boundary triangles include both
passes. Replay groups legacy records by inferred pass; historical generator
versions are unknown. Both checker and assembler use exported float32 geometry.

| Tile | Pass | Competing pairs before | After assembly |
| --- | --- | ---: | ---: |
| t0_-1_-1 | first | 0 | 0 |
| t0_-1_-2 | first | 0 | 0 |
| t0_-1_-3 | first | 6 | 0 |
| t0_-1_0 | first | 0 | 0 |
| t0_-1_1 | first | 0 | 0 |
| t0_0_-1 | first | 0 | 0 |
| t0_0_-2 | first | 6 | 0 |
| t0_0_0 | first | 17 | 0 |
| t0_0_1 | first | 8 | 0 |
| t0_1_-1 | full | 0 | 0 |
| t0_1_0 | first | 0 | 0 |
| t0_1_1 | first | 0 | 0 |
| **Total** | | **37** | **0** |

These are boundary-evidence counts, not full-tile rebuild counts. Geometry and
raw logs remain outside git under `$TURKU3D_WORK/reports/`. Reproduce with:

```sh
source ../sandbox.env
PYTHONPATH=pipeline:textures OPENBLAS_NUM_THREADS=1 bash pipeline/env/run.sh python tools/research/wall_invariant_audit.py --log "$TURKU3D_WORK/reports/oci-wall-log.txt" --evidence "$TURKU3D_WORK/reports/wall-evidence" --out "$TURKU3D_WORK/reports/wall-audit.json"
```

## Build behavior and precedence

Same-facing overlaps are resolved by polygon subtraction, retaining exposed
remainders, UVs, materials and normals. Full-pass facade paint precedes secondary
structures; first-pass building/roof geometry precedes structural layers. Already
admitted matching neighbour evidence precedes a new competing surface, under one
admission lock. Assembly preserves the existing collision surfaces and facade
assets. The tunnel cut changes both shell collision and visuals consistently.

The checker now projects onto the actual triangle plane and clips the common
polygon to the exact plane-distance band. This fixes the old vertical proxy for
slightly sloping triangles and detects partial overlap of diverging planes.
Surface checks return diagnostics; they never throw a tile-fatal exception.
Only contradictory building ownership remains a programming error. Existing
service behavior retains an already published structural pass if an appearance
upgrade fails.

Targeted validation:

```sh
PYTHONPATH=pipeline:textures PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_wall_surfaces.py pipeline/tests/test_coarse_tunnels.py pipeline/tests/test_facade_wall_overlap.py pipeline/tests/test_facade_programs.py
```
