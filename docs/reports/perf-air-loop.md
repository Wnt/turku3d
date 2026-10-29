# City-loop shader and residency fix

The supplied Air recording contains 5,765 GPU creation calls, including 2,941 shader
modules with only 212 unique label/code-length pairs. Its final resident inventory is
15 models and 1,337,192 triangles. These are observations from the existing recording,
not an after-fix benchmark.

Jonni's updated instruction cancelled Air reproduction/validation and scenario scripts.
The briefly started session was stopped immediately, and its uncommitted capture scripts
were removed. No completed before/after comparison is claimed; Jonni will test Pages.

## What changed

- r186 chooses matrix uniforms sized to each instanced batch. Three and seventeen cars
  therefore produced different vertex programs. Instanced draws now use r186's existing
  vertex-attribute path, including previous matrices for velocity and shadow variants.
  Each draw retains its own buffers; only compiled shaders/pipelines are reused.
- Exact untextured PBR materials are shared across tiles. Generated tree LOD materials
  also share reference-counted ownership. Authored textures and facade node graphs keep
  their existing bindings. Shared river materials survive another tile's departure.
- A bounded cache retains up to 256 recently released program entries and 256 pipeline
  entries. It retains compiled state, not scene objects, textures, geometry or bindings.
- Normal play draws only the current 3×3 window. The former 500 m retention halo kept
  visited models around spawn. Build-ahead requests no longer pin visible models; leaving
  the window invalidates unfinished visible loads, including during a service outage.
  Startup's explicit retained starts still work. Basic/full visual and collision pairs
  remain atomically prepared and published.
- Unloading emits object disposal as well as releasing geometry/materials. Parked
  inventory updates preserve existing batches and matrix buffers when capacity allows.
  Untouched draft trucks now leave the scene and release resources with their inventory.

## How to verify

Run from the assigned checkout with `source ../sandbox.env`:

```sh
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/streaming-resources.test.ts tests/unit/coarse-tiles.test.ts tests/unit/vegetation-lod.test.ts tests/unit/shadow-materials.test.ts tests/unit/compile-model.test.ts tests/unit/parked-streaming.test.ts tests/unit/truck.test.ts
```

The added regressions run the real r186 CPU shader builder and pipeline manager without
Chrome. They establish differing original shader text versus identical fixed text for
different instance counts, also with velocity output; compiled pipeline reuse after
unload; bounded idle caches; shared-material lifetime; stable parked draws; and identical
nine-model/144-collision-cell residency after a return to spawn. Existing paired basic/full
publication, shadow and vegetation checks remain selected.

Post-deploy selection (also without Chrome):

```sh
npm --prefix game test -- tests/unit/render.test.ts tests/unit/shadow-batch.test.ts tests/unit/ground-binding.test.ts tests/unit/ground-material.test.ts tests/unit/vehicle-fleet.test.ts
```

## Follow-ups

Jonni repeats his city loop on the deployed Pages game and reports frame pacing and
resident counts. Hardware frame-time or heap improvements have not been measured after
the change. The renderer adapters intentionally target the pinned three.js r186 API.
