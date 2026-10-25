# mk/

Each work area owns one `mk/<area>.mk`; the root `Makefile` includes `mk/*.mk` in sorted order. Use an area prefix for target names (`foundation-*`, `pipeline-*`, `game-*`, ...) and add `## description` after a target to list it in `make help`. `$(REPO)` is the repo root. `PIPELINE_PY` (pipeline venv python) is defined with `?=` in `foundation.mk`.
`tile.mk` owns the Phase 0 exit chain (`make tile`, `tile-*`); it uses variables from `game.mk`, `col.mk`, `pipeline.mk` and `qa.mk`, which sort before it.
