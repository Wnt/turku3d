# Developing Turku3D

[Project overview](../README.md) · [Documentation index](README.md) · [Contributor guide](../CONTRIBUTING.md)

Turku3D has three main runtime parts: **browser game** (`game/`), **multiplayer server** (`server/`), and an **on-demand city-data / tile pipeline** (`pipeline/`, `tools/`). The deployed game and generated world are separate systems.

## Quick start: browser client

A recent Node.js and npm are required; **Node 24** is used by the multiplayer server. The native geographic pipeline has additional, macOS/Apple Silicon-focused dependencies documented separately.

```sh
git clone https://github.com/Wnt/turku3d.git
cd turku3d
npm ci --prefix game
make game-dev
```

Open **http://localhost:5170/?scene=dev** for the synthetic development scene. `make game-dev` launches Vite and the local coarse-tiles service. The root Makefile defaults to port 5170; worktree sandboxes assign their own `TURKU3D_PORT_BASE`. On a development worktree, source its `sandbox.env` before running project tools.

Without the separately provisioned source-data tree and live generator, **do not assume the default Turku scene works locally**. A successful synthetic-scene launch does not verify real-city data.

```sh
make game-test    # Vitest unit tests
make game-build   # TypeScript check and production bundle
```

For affected browser checks, see [game/README.md](../game/README.md), [Playwright configuration](../game/playwright.config.ts), and [the project test/deployment policy](../AGENTS.md). Do not treat an unrun full browser suite as passing.

## Multiplayer server

```sh
npm ci --prefix server --ignore-scripts
npm test --prefix server
node server/index.js
```

The server defaults to loopback `127.0.0.1:7700`, not a publicly exposed endpoint. Its HTTP health route is `/healthz`. For an isolated run use `TURKU_MP_PORT=0` and a temporary `TURKU_MP_SCORES` directory. See [server setup](../server/README.md) and [network protocol](../server/PROTOCOL.md). The public service also depends on a separately configured proxy/tunnel.

## Real Turku world and data pipeline

The generated game world uses City of Turku open data (lidar, buildings, streets and orthophotos) and OpenStreetMap. **Bulk source data, generated tiles and secrets are not part of Git.** Shared inputs normally live under `$TURKU3D_DATA/raw` and each sandbox writes to `$TURKU3D_WORK`.

The real-data tooling includes Python 3.12/`uv` and native geospatial dependencies. Start with [architecture](ARCHITECTURE.md), [source manifests](../data-manifests/sources.yaml), [pipeline environment](../pipeline/env/README.md), and [developer operations](../scripts/dev/README.md) rather than running old one-off AOI build recipes. `make help` enumerates the current targets.

Generated tile content is requested on demand in production. Pages does **not** ship a fixed, full-city tile bundle. Consult the linked runbooks before publishing, warming, or operating remote services.

## Source and deployment boundaries

| Path | Responsibility |
| --- | --- |
| `game/src/` | Browser engine, renderer, input, physics, gameplay, UI and network client |
| `server/` | Multiplayer presence, shared vehicle state, hourly scores |
| `pipeline/` | Source-data processing, materials and geographic generation |
| `tools/` | Local and remote tile services, developer utilities |
| `scripts/dev/` and `mk/` | Isolated worktrees, test selection, builds and publication |
| `docs/specs/` | Data and coordinate contracts |
| `docs/reports/` | Dated test and research findings |

The public client is hosted at [wnt.github.io/turku-game](https://wnt.github.io/turku-game/) from the deploy-only [Wnt/turku-game](https://github.com/Wnt/turku-game) repository. Its `game/` files are build outputs. Change the source here; project deployment tooling handles publication after integration. A PR does **not** publish to Pages.

For small changes, run only relevant tests, explain what you did and did not verify, and submit a PR. The project's automated task orchestration and merge/deploy rules are described in [AGENTS.md](../AGENTS.md); that workflow assumes the owner's configured data, machines and credentials and is **not** a prerequisite for external contributors.
