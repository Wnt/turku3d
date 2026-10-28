# Turku3D

An open-world game of central Turku, Finland, for desktop Chrome (three.js r186 `three/webgpu` + Rapier 0.21 WASM
worker + 3DTilesRendererJS). The world is built from the City of Turku's open data (buildings, lidar, street areas,
aerial imagery) and OpenStreetMap; facade and material textures are AI-generated. Walk and drive around Läntinen
Rantakatu 15 with an Xbox controller or keyboard and mouse.

**Play:** https://wnt.github.io/turku-game/ (deploy repository [Wnt/turku-game](https://github.com/Wnt/turku-game)).

## Multiplayer server

The [multiplayer server](server/README.md) targets osgallery-dev behind
`wss://turku.lab.madekivi.fi`, providing presence and Finnish-hour delivery
scoreboards for the later browser client. See its [protocol](server/PROTOCOL.md)
and the [project architecture](docs/ARCHITECTURE.md).

## Run locally

The game needs world tiles built by the pipeline from the Turku open data sources
([`data-manifests/sources.yaml`](data-manifests/sources.yaml)); no data is committed here. On a Mac (Python 3.12,
Node 22, `uv`):

```sh
make help                      # every target
make fetch-aoi AREA=work-3x3   # download the open data once into ~/turku3d/data/raw
make refresh && make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'
make game-preview             # built game + local Turku tiles (default); ?scene=dev for the test world
cd game && npm install && npm run dev     # the dev server alone runs on the synthetic dev tiles
```

Licences: the code is MIT ([LICENSE](LICENSE)); third-party data and assets are listed in [NOTICE.md](NOTICE.md).
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
