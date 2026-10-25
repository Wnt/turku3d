# Structures: office-core publication and assembly

This iteration publishes the existing structures generator through `turku3d.layers`
v1 and verifies the 250 m office core with main's plain ground. Strict reconstruction
accuracy remains separately reported; assembly success does not establish full coverage.

## Changes

- Shared collision, visual and manifest publishers; complete objects assigned once
  to footprint-centroid tiles with `params.seams=objects`. Grid clipping and its
  obsolete writer were removed. Tests preserve original cross-grid triangles exactly.
- Default `--area office-block` uses the 16 collision tiles c_1_2 through c_4_5.
- Ground snapshots verify v1 declared hashes and ignore unlisted stale tiles.
  Missing quay classification in plain ground produces no invented features.
- Ground contacts use a triangle-bounds index, avoiding global-radius searches
  widened by large river-bed triangles. Barycentric contact checks are unchanged.
- The gamepad functional tests inherit the existing 120-second project deadline
  instead of imposing 25 seconds under host swapping. Assertions are unchanged.
- Main's task-stop fix superseded the earlier local fix during rebase.

## Real evidence

Input is read-only `/Users/wnt/turku3d/data/build/layers/ground`, with a verified
snapshot in this sandbox. Ground acceptance is true; the exact source hash and
snapshot are in [structures-core.json](structures-core.json).

The build took **190.843 seconds**. It published **13 collision layer files,
4 visual layer files and 4,074 triangles**: **91 fences, two walls and one stair
flight**. Main's plain ground supplied no classified quays or ramp triangles.

Strict acceptance remains **BLOCKED**: **59/94 Rapier probes passed** (57/91 fences,
2/2 stair traversals, 0/1 blocking wall). There are **271 reported issues**, including
142 uncovered ramp survey lines and 35 failed probes. The low wall is below the
blocking-test threshold. No tolerances were relaxed and rejected objects remain
explicitly reported. The CLI returns nonzero for these measured acceptance failures
while retaining the intermediate layer for integration.

`make tile-build AREA=office-block T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"` **passed** against
this layer and a sandbox copy of main's 16 core ground collision tiles. It emitted
**16 shipped collision tiles and nine visual tiles** (four L0 plus coarse parents)
and verified the manifest, tile hashes and seams. Output:
`$TURKU3D_WORK/dist/tiles/manifest.json`. No main or other sandbox output was modified.

## Reproduce

```sh
source ../sandbox.env
make structures-build ARGS="--area office-block --ground-layer $TURKU3D_DATA/build/layers/ground"
# Expected nonzero until the strict evidence issues above are resolved.
make -j2 structures-test
scripts/dev/gate
```

For the same core-only assembly, stage ground with shared readers/publishers:

```sh
bash pipeline/env/run.sh python - <<'PY'
from turku3d import config as C
from turku3d.layers import read_manifest, read_collision, read_visual, write_collision, write_manifest
from turku3d.fetch.common import atomic_write_bytes
from turku3d.structures.layer import sha256
source = C.data_root() / 'build/layers/ground'
out = C.work_root() / 'build/layers/ground'
m = read_manifest(source)
files = []
for tile in C.OFFICE_BLOCK.collision_tiles:
    name = tile.id + '.col.npz'
    if name in m['tiles']:
        write_collision(out / name, read_collision(source / name))
        files.append(name)
for tile in C.OFFICE_BLOCK.visual_tiles:
    name = tile.id + '.vis.glb'
    if name in m['tiles']:
        atomic_write_bytes(out / name, read_visual(source / name))
        files.append(name)
write_manifest(out, inputs={'main_ground': sha256(source / 'MANIFEST.json')},
    params={'seams': 'continuous', 'area': 'office-block', 'collision_scope': 'core'},
    code=m['code'], tiles=files)
PY
make tile-build AREA=office-block T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```

## Verification

Focused gate: GREEN, 36.582 seconds; 23 structures Python tests and 13 Rapier tests.
The first full gate found missing newly landed environment dependencies and the
local gamepad deadline. Dependencies were installed according to the current
pipeline extras and streetlevel requirements. Corrective checks passed:
147 pipeline tests in 9.13 s, 61 texture tests (one skip) in 3.62 s, and all six
gamepad browser tests in 49.0 s. The consolidated gate runs with serialized stages
and at most two test workers, retaining every configured check.

Full logs and physics cases remain under `$TURKU3D_WORK/reports/structures/`.

## Remaining evidence work

Resolve the rejected stair/wall measurements, uncovered ramps and 35 failed barrier
probes against the saved cases. Supply classified quay profiles when that ground
work resumes. Add the assembled stairs validation sidecar during full AOI acceptance.
