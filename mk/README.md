# mk/

Each work area owns one `mk/<area>.mk`; the root `Makefile` includes `mk/*.mk` in sorted order. Use an area prefix for target names (`foundation-*`, `pipeline-*`, `game-*`, ...) and add `## description` after a target to list it in `make help`. `$(REPO)` is the repo root. `PIPELINE_PY` (pipeline venv python) is defined with `?=` in `foundation.mk`.
`tile.mk` owns the Phase 0 exit chain (`make tile`, `tile-*`); it uses variables from `game.mk`, `col.mk`, `pipeline.mk` and `qa.mk`, which sort before it.

The root Makefile exports the `TURKU3D_*` sandbox contract. New targets must derive outputs from
`TURKU3D_WORK` and ports from `TURKU3D_PORT_BASE`; use `GAME_DIST` for the Vite build and `DIST_PORT`
for static serving. npm and uv caches are sandbox-local. Source `../sandbox.env` in a worker, then
`make tooling-test` to check the tooling. See `scripts/dev/README.md` for the complete contract.
