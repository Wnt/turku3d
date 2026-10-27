# Texture tooling (textures/): Codex runner, Street View facade references, post-processing, pilot.
# Runs on the Mac ([M]): Codex CLI 0.160 is required for gpt-6-astra (the remote 0.144 is too old).
TEX_DIR      := $(REPO)/textures
TEX_VENV     ?= $(TEX_DIR)/.venv
TEX_PY       ?= $(TEX_VENV)/bin/python
TEX_DATA     ?= $(shell $(TEX_PY) -c 'from turkutex.paths import textures_data_dir; print(textures_data_dir())' 2>/dev/null)
TEX_PILOT    ?= $(TEX_DATA)/pilot
TEX_J        ?= 6
TEX_MAX_PCT  ?= 100

.PHONY: textures-venv textures-setup textures-deepbump textures-test textures-usage textures-facades \
        textures-refs textures-run textures-post textures-pilot textures-pilot-previews

$(TEX_PY):
	uv venv --python 3.12 $(TEX_VENV)
	uv pip install --python $(TEX_PY) -e '$(REPO)/pipeline' -e '$(TEX_DIR)[post,ground,dev]' -r $(TEX_DIR)/requirements-streetlevel.txt
	uv pip install --python $(TEX_PY) --no-deps streetlevel==0.12.13

textures-venv: $(TEX_PY) ## Create textures/.venv (py3.12; streetlevel --no-deps, onnxruntime, scipy, pytest)

textures-deepbump: $(TEX_PY) ## Clone DeepBump (GPL-3, ONNX) to <data>/tools/DeepBump (not vendored)
	$(TEX_PY) -c 'from turkutex.post.maps import ensure_deepbump; print(ensure_deepbump())'

textures-setup: textures-venv textures-deepbump textures-ktx ## venv + native KTX tools + DeepBump in sandbox

textures-test: $(TEX_PY) ## Run textures tests (fake codex; no network, no quota)
	cd $(TEX_DIR) && $(PIPELINE_RUN) $(TEX_PY) -m pytest -q -n $(TURKU3D_JOBS)

textures-usage: $(TEX_PY) ## Codex weekly quota snapshot (free, no model call)
	$(TEX_PY) -m turkutex codex usage

textures-facades: $(TEX_PY) ## Street facades near a point: make textures-facades E=.. N=.. OUT=f.geojson
	$(TEX_PY) -m turkutex facades --near $(E) $(N) --radius $(or $(R),70) --out $(OUT)

textures-refs: $(TEX_PY) ## Street View reference crops: make textures-refs FACADES=f.geojson [OUT=dir]
	$(TEX_PY) -m turkutex refs $(FACADES) $(if $(OUT),--out $(OUT))

textures-run: $(TEX_PY) ## Codex batch: make textures-run JOBS=j.jsonl OUT=dir [TEX_J=4 TEX_MAX_PCT=100]
	$(TEX_PY) -m turkutex codex run $(JOBS) --out $(OUT) -j $(TEX_J) --max-pct $(TEX_MAX_PCT)

textures-post: $(TEX_PY) ## Post-process: make textures-post IN=x.png OUT=dir [TILE=xy|x|none MAT=..]
	$(TEX_PY) -m turkutex post $(IN) --out $(OUT) --tile $(or $(TILE),xy) --material $(or $(MAT),facade_mixed)

textures-pilot: $(TEX_PY) ## Office-block pilot end to end (cached/resumable; uses Codex quota once)
	mkdir -p $(TEX_PILOT)
	$(TEX_PY) -m turkutex codex usage >> $(TEX_PILOT)/usage_log.jsonl || true
	$(TEX_PY) -m turkutex refs $(TEX_DIR)/pilot/facades.geojson
	$(TEX_PY) -m turkutex jobs label $(TEX_DIR)/pilot/facades.geojson --refs $(TEX_DATA)/refs --out $(TEX_PILOT)/label_jobs.jsonl
	$(TEX_PY) -m turkutex codex run $(TEX_PILOT)/label_jobs.jsonl --out $(TEX_PILOT)/run_label -j 2 --max-pct $(TEX_MAX_PCT)
	$(TEX_PY) -m turkutex jobs improve $(TEX_DIR)/pilot/facades.geojson --refs $(TEX_DATA)/refs --labels $(TEX_PILOT)/run_label/out.jsonl --out $(TEX_PILOT)/improve_jobs.jsonl
	$(TEX_PY) -m turkutex codex run $(TEX_PILOT)/improve_jobs.jsonl --out $(TEX_PILOT)/run_improve -j 2 --max-pct $(TEX_MAX_PCT)
	$(TEX_PY) -m turkutex codex usage >> $(TEX_PILOT)/usage_log.jsonl || true
	$(TEX_PY) -m turkutex post $(TEX_PILOT)/run_improve/images/lr15_se.improve/00.png --out $(TEX_PILOT)/post/lr15_se --name lr15_se --tile x --size 2048x512 --material wood_painted
	$(TEX_PY) -m turkutex post $(TEX_PILOT)/run_improve/images/aurakatu2_se.improve/00.png --out $(TEX_PILOT)/post/aurakatu2_se --name aurakatu2_se --tile none --size 2048x1024 --material render
	$(MAKE) textures-pilot-previews

textures-pilot-previews: $(TEX_PY) ## Rebuild docs/pilot/textures previews + library/manifest.jsonl
	cd $(TEX_DIR) && $(TEX_PY) pilot/make_previews.py

.PHONY: textures-ktx textures-facade-inventory textures-facade-batch textures-library-batch
textures-ktx: $(TEX_PY) ## Pinned native KTX tools in the sandbox (Mac arm64; no system install)
	$(TEX_PY) -m turkutex.post.setup_ktx

textures-facade-inventory: $(TEX_PY) ## Interim AOI wall inventory; replace with W1-03 facades.geojson
	$(TEX_PY) -m turkutex facade-inventory --out $(OUT)

textures-facade-batch: $(TEX_PY) ## AOI production: FACADES=... [STAGE=all|refs|label|improve|post|report]
	$(TEX_PY) -m turkutex facade-batch $(or $(STAGE),all) $(FACADES) -j $(TEX_J) --max-pct $(TEX_MAX_PCT)

textures-library-batch: $(TEX_PY) ## Missing library materials required by FACADES=... (6 Codex workers)
	$(TEX_PY) -m turkutex library-batch $(FACADES) -j $(TEX_J) --max-pct $(TEX_MAX_PCT)

# W1-10 ground products. Source the sandbox environment before invoking these targets.
GROUND_OUT ?= $(if $(TURKU3D_WORK),$(TURKU3D_WORK)/textures,$(error Source the sandbox.env file before building ground textures))
GROUND_OSM ?= $(TURKU3D_DATA)/raw/osm/work-3x3/work-3x3-261024.parquet
GROUND_PY = PYTHONPATH=$(REPO)/textures:$(REPO)/pipeline bash $(REPO)/pipeline/env/run.sh python

.PHONY: ground-texture-test ground-python-test ground-material-test ground-render-test ground-qa ground-jobs ground-library ground-textures ground-markings ground-acceptance

ground-texture-test: ground-python-test ground-material-test ## Fast ground unit + material contract/type checks (make -j2)

ground-python-test:
	$(GROUND_PY) -m pytest -q $(TEX_DIR)/tests/test_ground.py

ground-material-test: game-install
	$(GAME_NPM) run typecheck
	$(GAME_NPM) exec vitest run tests/unit/ground-material.test.ts

ground-render-test: game-install ## Compile/render the ground shader in Chrome WebGPU + WebGL2 (sandbox profile)
	cd $(GAME_DIR) && node scripts/ground-qa.mjs

ground-qa: game-install ## Three real-ortho views before/after, both backends; requires generated assets
	cd $(GAME_DIR) && node scripts/ground-qa.mjs --real

ground-jobs: ## Generate the eight image_gen job specifications (no model call)
	$(GROUND_PY) -m turkutex.ground.jobs --out $(GROUND_OUT)/ground-jobs.jsonl

ground-library: ## Post-process runner output and encode eight PBR materials + three KTX2 arrays
	$(GROUND_PY) -m turkutex.ground.library --run $(GROUND_OUT)/ground-run --out $(GROUND_OUT)/library

ground-textures: ## Four L0 ortho/splat products; pass GROUND_LAYER for ground .col.npz semantics
	$(GROUND_PY) -m turkutex.ground.build --out $(GROUND_OUT)/ground $(if $(GROUND_LAYER),--ground-layer $(GROUND_LAYER))

ground-markings: ## OSM crossing paint clipped to city carriageways, with unresolved-source report
	$(GROUND_PY) -m turkutex.ground.markings --osm $(GROUND_OSM) --out $(GROUND_OUT)/ground/markings.geojson

ground-acceptance: ## Real deliverable audit; fails on absent ground semantics/marking kinds/QA evidence
	$(GROUND_PY) -m turkutex.ground.acceptance --root $(GROUND_OUT) --qa $(TURKU3D_WORK)/reports/ground-qa/real.json
