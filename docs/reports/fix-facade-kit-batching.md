## Result: DONE

## What changed

Facade kit parameters now travel in per-vertex attributes instead of per-style materials.
Published and draft packaging use the same binder and share one kit material per tile/layer.
The WebGPU/WebGL2 TSL material reads module, bay/storey spacing, UV origin and linear wall/trim
colours from those attributes. Metre UVs and batch object ranges are retained. Existing
published v1 materials remain readable until rebuilt. Roof surfaces retain library bindings.
Outer LOD2 wall/roof nodes keep their original `outer-lod2-<id>` object id and surface metadata.

Added a small synthetic binder/batcher test covering different styles in one batch, retained
parameters, linear colour conversion, metre UVs and object ranges. Updated area READMEs.

## How to verify

```sh
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- bash -c 'cd pipeline && bash env/run.sh python -m pytest -q tests/test_assemble.py tests/test_assemble_outer.py tests/test_facade_kit.py tests/test_coarse.py'
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Requested pipeline tests: **35 passed**, one existing rasterio deprecation warning, 92.88 s.
Fast gate: **GREEN**, 31.101 s (26.757 s environment dependency synchronization).
Type checking and the new synthetic Node regression test also passed. No browser runs.
The first pipeline attempt found missing local validator dependencies; installing the locked
sandbox dependencies resolved that failure without changing checks.

## Follow-ups

None. The runner lands this committed branch at turn end.
