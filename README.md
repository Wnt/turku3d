# Turku open world

GTA-V-fidelity open world of Turku for desktop Chrome (three.js r186 `three/webgpu` + Rapier 0.21 WASM worker + 3DTilesRendererJS).
Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); formats are in [docs/specs/](docs/specs/).

```
make help                 # every target (each area adds its own in mk/<area>.mk)
make foundation-venv      # pipeline/.venv via uv (python 3.12)
make foundation-test      # config unit tests
make tile                 # Phase 0 chain: fetch (cached) -> stub .col + GLB -> tileset/manifest -> sync -> validator -> Chrome
```

How to run everything (tests per area, data locations, known gaps): [docs/STATUS.md](docs/STATUS.md).

| Dir | What |
|---|---|
| `docs/` | architecture, research reports, specs (`col-format.md`, `materials.md`, `tiles.md`) |
| `pipeline/` | Python package `turku3d` (config = single source of truth for origin/CRS/tiles/paths) |
| `textures/` | Codex + local material tooling (Mac) |
| `game/` | Vite + three/webgpu + Rapier game |
| `tools/` | probes, validator (`tools/validator`), dev server + sync (`tools/dev`) |
| `qa/` | visual QA loop (D8): reference views, render driver, Codex judge, reports |
| `data-manifests/` | small committed manifests (LAZ index, AOIs, checksums) |
| `mk/` | per-area Make fragments, included by the root `Makefile` |

Bulk data is never committed: shared raw inputs live in `~/turku3d/data/raw`; sandbox build products live under `$TURKU3D_WORK`. The pipeline runs locally on the Mac.
