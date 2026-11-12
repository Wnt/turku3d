# W1-09: Photo-referenced facade textures for every AOI building (Phase 1 task 1.8)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `textures/`.

## Goal

Street facades look like the real buildings: same bay rhythm, colours, materials and ornaments, cleaned of cars,
people, trees and shadows. Visual detail need not be 1:1, but each building should be recognisable. Courtyard facades
and roofs come from the material library. Codex (gpt-6-astra with image_gen) does the image work; local code does
everything deterministic. The Phase 0 pilot in `textures/` and `docs/research/ai-textures.md` (including its
Verification section) is the starting point.

## Inputs

- The facade list `$TURKU3D_WORK/build/layers/buildings/facades.geojson` and its spec in `docs/specs/facades.md`, both
  from W1-03.
  - Until that lands, derive facades from the kantakartta footprints the way the pilot does.
  - Then switch to W1-03's list and delete the interim path.
- Panos:
  - Google Street View (cached under `$TURKU3D_DATA/raw/streetview` and `~/turku3d/research/streetview`)
  - Apple Look Around
  - **Mapillary** (token in `~/.config/turku3d/mapillary.token`; never print it or commit it), via the Graph API v4
    with `Authorization: OAuth <token>`; verified working
  - Google Maps user photospheres via `tools/refs/`

## Deliverables

1. **Reference crops for every street facade**: the best 2–4 views from all sources (angle, distance, recency),
   perspective-rectified, with rough occluder masks.
2. **Codex jobs at scale**, through the existing runner (quota check at 100 %, resumable, content-hash cache):
   - A strict-schema label job per building, which produces `spec.json` (bay counts are priors only; take counts from
     W1-03 geometry).
   - An improve job per street facade, which produces a cleaned rectified facade texture.
   - Library generation for missing materials: roofs, courtyard facades, plinth stone, ground materials.
   - Run with high parallelism: 6–8 concurrent Codex runners.
3. **Local post-processing**:
   - Scale the texture to the facade metres (u/v convention, COMMON §4).
   - Make-tileable and low-frequency flatten for library textures.
   - DeepBump normals, ORM, and KTX2.
   - Write to `~/turku3d/data/textures/facades/<building_id>/<facade_index>.{ktx2,json}` and
     `textures/library/<material_key>/`.
4. **QA**: a contact-sheet HTML page of every facade (reference crop beside the result) and seam and scale checks.
   Log the Codex usage before and after, and the calls per facade.

## Acceptance

- Every street-facing facade in the AOI has a cleaned texture with ≥ 1 logged reference.
- Every other facade and roof has a library material.
- A contact sheet is attached in the result.
- Quota used is reported. Re-running is a no-op (cache).
