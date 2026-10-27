# tool-public-release: photo-derived facades retired

Date: 2026-10-27. Sandbox: `tool-public-release` (Claude worker). User decision 2026-10-27 10:00:
only generated-style facades stay in the game; maps cut from Google Street View or Apple Look Around
photos are removed. Street imagery remains a local reference for generation and is never published.

## Retirement

Classification of each bound entry in `$TURKU3D_DATA/facade-approvals/assembly/MANIFEST.json`, by
its sidecar JSON:

| Class | Rule | Count | Action |
|---|---|---:|---|
| Photo-derived | `method: "rectified-photo-bay-inpaint-v1"` | 283 | retired |
| Codex-improved photo | `imagegen_calls` without `source_mode: "generate"` + `generated_qa` | 28 | retired |
| Generated style | `source_mode: "generate"` with `generated_qa` | 33 | kept |

`bound` was 344 at retirement time (the `w1-09h` rollout had replaced three photo maps and added
three generated walls since the brief's 341 count). `turkutex` has no retire command, so the 311
entries (933 KTX2 maps and their sidecars) were moved under the export lock to
`retired/2026-10-27-photo-derived/<building>/` with `retired.json` listing the ids and the reason.
`MANIFEST.json` was rewritten atomically with `bound` = the 33 kept ids; `inventory_sha256` and
`library` are unchanged. Retired walls fall back to their library material (`render_smooth` and
the plinth/roof keys), as `assemble.facade_bindings` only binds listed ids.

`live_build.seed` used to merge the approvals over the published `textures/assembly` copy, so a
retired facade's files stayed in every later release. It now replaces the assembly with the
approvals (test: `test_seed_drops_facades_retired_from_the_approvals`).

## Four office tiles after retirement

Sandbox build: `make refresh` (all cached), `make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'`
reports `facades_bound: 33`.

| Tile | Wire bytes before | Wire bytes after | Before packer reduction (before → after) |
|---|---:|---:|---:|
| t0_0_0 | 7,598,637 | 7,524,835 | 12,503,667 → 7,524,835 |
| t0_0_1 | 7,775,087 | 7,744,852 | 9,477,176 → 7,744,852 |
| t0_1_0 | 7,461,586 | 6,703,782 | 8,957,081 → 6,703,782 |
| t0_1_1 | 7,597,679 | 7,534,857 | 10,178,107 → 7,534,857 |

All four are below the 8 MB cap without facade density reduction. The tile dist is 100 MB.
`tests/e2e-tile/area.spec.ts` passes on both backends: street maximum 328 draws (southwest-4),
aerial 260, bridge gameplay 458 (limit 500). Five fixed street/river poses render the same draw
counts before and after (169–329). Screenshots `before-*` / `after-*` for WebGPU and WebGL2 are in
`$TURKU3D_WORK/reports/tool-public-release/`; `compare-before-after.jpg` puts three side by side.

## Provenance audit of the other published textures

No other published texture class uses street-level photos:

- Ground (`textures/ground/MANIFEST.json` `source_licenses`): City of Turku 2022 true ortho and
  2025 ortho aerial imagery and street-area geometry (CC BY 4.0), OpenStreetMap crossings (ODbL),
  eight `image_gen` ground material albedos generated from text prompts.
- Library materials (`assembly/library/*/material.json`, `turkutex.library_batch`): one `image_gen`
  call per material from a text-only prompt; no reference image is attached.
- Prop and sign faces (`pipeline/turku3d/props`): procedurally drawn from the sign codes, not
  extracted from photos.
- UI art (`game/public/assets/ui`): generated with the Codex image runner.

The ground orthophotos are aerial imagery under CC BY 4.0 and need the City of Turku attribution
in the public release.

## GitHub Pages

Deploy-only public repository `Wnt/turku-game`, branch `gh-pages`, site
https://wnt.github.io/turku-game/. `scripts/dev/publish-pages` (module `scripts/dev/pages.py`)
copies the live release's `game/` (11 MB) and `tiles/` (100 MB) with a landing redirect, README,
ATTRIBUTION and `.nojekyll` into one parentless commit and force-pushes it; the integrator runs it
after every green publish when `$TURKU3D_DATA/publish/pages.json` exists. The first manual publish
of release `18cb107` (33 generated facades, 307 files, 116.2 MB) took 14.3 s including the push;
the Pages build took 3.7 s. A release that binds any non-generated facade is refused.

That first site still carried a game bundle built with the absolute `/` base, so its scripts 404
under `/turku-game/`. This change builds the game with a relative base; a local check of the
assembled site under `/turku-game/` from a plain static server without COOP/COEP
(`crossOriginIsolated` false) loaded and walked on WebGPU and WebGL2 with no page errors. The game
uses no `SharedArrayBuffer`. The next green integration republishes the site with that build.
