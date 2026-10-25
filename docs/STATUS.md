# Status and how to run everything

Updated 2026-10-25 (Phase 0 integration). Plan and decisions: [ARCHITECTURE.md](ARCHITECTURE.md) (Phase 0 status table in §4).
All commands run from the repo root `/Users/wnt/Documents/game-test` on the Mac; `make help` lists every target.
Remote targets sync the code to `osgallery-dev:~/turku3d/repo` first and run inside `~/turku3d/envs/pipeline`.

## Phase 0 exit: one command

```bash
make tile                      # T=t0_0_0 (the office tile), ~75 s
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"   # the whole Phase 1 AOI (4 L0 tiles, 64 collision tiles)
make tile-show                 # play it: http://localhost:8080/?tiles=/tiles/tileset.json  (&backend=webgl)
make tile-qa                   # render the qa/views registry against the game (color + id); JUDGE=1 calls Codex
```

`make tile` = `tile-fetch` (remote `turku3d.fetch all`, cached) → `tile-build` (remote `turku3d.package.stub_tile`:
stub ground `.col` ×16 + ortho-draped ground GLB, `tileset.json`, `manifest.json`, hash check) → `tile-sync`
(`tools/dev/sync.sh` → `~/turku3d-data/dist`, sha256-verified) → `tile-validate` (Node Rapier validator, report in
`~/turku3d-data/reports/validate-<tile>.json`) → `tile-e2e` (game build + Playwright in Chrome, WebGPU and WebGL2:
the tile renders, the character walks on the stub collision, QA render contract + ID pass). Screenshots:
`game/test-results/screenshots/tile-*.png`.

## Tests per area (all green on 2026-10-25)

| Area | Command | Result |
|---|---|---|
| foundation (config, specs) | `make foundation-test` | 17 passed |
| pipeline, local (fetch, package) | `make pipeline-test` | 38 passed, 3 skipped (gdalbuildvrt, osmium, scipy not on the Mac) |
| pipeline, remote (full conda env) | `make pipeline-test-remote` | 41 passed |
| packaging / stub tile only | `make tile-test` | 7 passed, 1 skipped (scipy) |
| `.col` + validator | `make col-test` | pytest 15 passed; node 22 passed |
| validator on fixtures (2000 walkers) | `make col-validate-fixtures` | all 6 checks PASS (~42 s) |
| game unit (vitest) | `make game-test` | 25 passed (incl. game KCC on validator fixtures) |
| game e2e on dev tiles (Chrome, both backends) | `make game-e2e` | 6 passed |
| game e2e on pipeline stub tiles | `make tile-e2e` | 4 passed |
| tools/dev serve-dist | `make game-dev-tools-test` | 5 passed |
| QA tooling (no network, no Codex) | `make qa-test` | pytest 17 passed; node 4 passed |
| textures (fake codex) | `make textures-test` | 29 passed |
| remote env check | `make pipeline-versions` | exits 0 |

First-time setup: `make foundation-venv pipeline-venv col-deps game-install qa-venv textures-setup` (Mac) and
`make pipeline-bootstrap` (remote, idempotent).

## Data

| Where | What |
|---|---|
| `osgallery-dev:~/turku3d/data/raw/{laz,wfs,ortho,osm}` | office-block AOI downloads (`make fetch-aoi`, `make fetch-report`; checksums in `data-manifests/checksums/`) |
| `osgallery-dev:~/turku3d/data/dist/tiles` | built tiles (`make tile-build`) |
| `~/turku3d-data/dist/tiles` | Mac mirror (`make tile-sync` / `make game-sync`) |
| `~/turku3d-data/raw/streetview`, `~/turku3d-data/textures` | Street View cache, texture pilot outputs (textures/) |
| `data/qa/` (git-ignored) | QA reference images (`make qa-refs` re-downloads), renders, judge outputs, reports |

## Codex quota

`make qa-usage` or `make textures-usage` (no model call). Runners stop at `--max-pct` / `QA_CODEX_CAP` (default 100 %)
or when usage cannot be read. Used so far: about 1 % of the weekly window (17 calls: 4 texture pilot, 13 QA judge).

## Known gaps after Phase 0

- Stub tiles only: no curbs, stairs, buildings, trees, water, KTX2 or meshopt in the GLB; the river is interpolated
  across. The validator's buildings/curbs/stairs checks are therefore skipped on them (they pass on the fixtures).
- Validator: no car checks (Task 1.14), no seam-identity check (the stub builder's seams are bitwise identical by
  construction and a pytest checks it).
- `toktx` (KTX-Software) not installed on the remote; KTX2 is encoded on the Mac with `ktx2-encoder`.
- Rapier 0.21 reports `featureId = 0` for trimesh ray hits; the game maps hits to triangles with
  `game/src/physics/triLocator.ts`, so `game/src/shared/col/rapierBuild.ts#hitMaterial` must not be used for materials.
- QA: the 3 registry views are Street View poses at medium confidence (not lidar-registered); `office_front` stands in
  t0_0_1, so render the whole AOI (`make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"`) before `make tile-qa`.
- Codex quota cost per call unknown (the usage counter is an integer percent).
