# Packaging and Phase 0 tile builder

```sh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-test
bash pipeline/env/run.sh python -m turku3d.package.stub_tile --synthetic --out "$TURKU3D_WORK/dist/test-tiles"
```

The local builder reads maanpinta ground key points, GIS:Katualueet and the 2022 true ortho
from shared `raw/`. It writes 16 `.col` files and one ortho-draped GLB per L0 tile,
then `tileset.json` and `manifest.json` under `$TURKU3D_WORK/dist/tiles`.
Meshopt encoding uses the pinned npm encoder from the native toolchain.

All real L0 builds slice one cached, canonical area heightfield (1.25 m spacing).
The key includes source paths, sizes, nanosecond mtimes, grid parameters and interpolation
code. Raw files are immutable; changed inputs invalidate the cache. Both visual and collision
use the same heightfield. `AOI_BORDER` marks only the exterior of the four-tile Phase 1 envelope,
never the interior L0 seams. The CLI decodes and compares every shared collision edge bitwise,
as well as verifying all manifest hashes, before reporting success.

This is coarse Phase 0 ground: no curbs, buildings, stairs or water surface. Wave 1's assembler
will replace the stub with the layer composition described in `docs/briefs/COMMON.md`.
