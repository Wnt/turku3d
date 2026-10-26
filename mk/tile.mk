# Local chain, deliberately ordered even under make -j.
TILE_IDS = $(or $(T),t0_0_0)
DIST_MIRROR ?= $(TURKU3D_WORK)/dist
TILE_REPORTS ?= $(TURKU3D_WORK)/reports
TILE_WALKERS ?= 2000
comma := ,

.PHONY: tile tile-fetch tile-build tile-validate tile-e2e tile-show tile-test tile-qa
tile: ## Assemble published layers, validate physics and run Chrome e2e locally
	$(MAKE) tile-build
	$(MAKE) tile-validate
	$(MAKE) tile-e2e

tile-fetch: ## Check cached work-3x3 inputs without network or writes to shared raw
	$(PIPELINE_RUN) python -m turku3d.fetch.cached --area work-3x3

tile-build: ## Build T into TURKU3D_WORK/dist/tiles and verify hashes and seams
	python3 $(REPO)/scripts/dev/host_slots.py build -- $(PIPELINE_RUN) python -m turku3d.package.assemble --area $(AREA) --tile $(TILE_IDS)

tile-validate: col-deps ## Node Rapier validation (TILE_WALKERS=2000)
	mkdir -p $(TILE_REPORTS)
	cd $(VALIDATOR) && node src/cli.ts --walkers $(TILE_WALKERS) --workers 4 \
	  --require buildings,curbs,cars$(if $(wildcard $(TURKU3D_WORK)/build/layers/structures/MANIFEST.json),$(comma)stairs,) --out $(TILE_REPORTS)/validate-aoi.json \
	  $(if $(wildcard $(TURKU3D_WORK)/build/layers),--layers $(TURKU3D_WORK)/build/layers) \
	  $(VALIDATOR_ARGS) $(or $(VALIDATOR_INPUTS),$(foreach tile,$(TILE_IDS),$(DIST_MIRROR)/tiles/collision/$(tile)))

tile-e2e: game-install ## Build the game and test real tiles with Chrome WebGPU and WebGL2
	$(MAKE) game-build
	cd $(GAME_DIR) && E2E_TILE_PORT=$(DIST_PORT) TILE_DIST=$(DIST_MIRROR)/tiles python3 $(REPO)/scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts

tile-show: game-build ## Serve the local result on the sandbox dist port
	@echo "open http://localhost:$(DIST_PORT)/?tiles=/tiles/tileset.json"
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIST) --port $(DIST_PORT) --mount /tiles=$(DIST_MIRROR)/tiles

tile-test: ## Offline packaging tests with synthetic inputs
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q -n $(TURKU3D_JOBS) tests/test_package.py tests/test_layers.py tests/test_assemble.py tests/test_assemble_outer.py
	node --test $(REPO)/pipeline/turku3d/package/prop_atlas.test.mjs

TILE_QA_PORT ?= $(DIST_PORT)
tile-qa: qa-venv game-build ## Render local tiles (colour+ID); JUDGE=1 enables quota-guarded parallel judging
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIST) --port $(TILE_QA_PORT) --mount /tiles=$(DIST_MIRROR)/tiles & pid=$$!; \
	  trap 'kill "$$pid" 2>/dev/null || true' EXIT; sleep 1; kill -0 "$$pid" || exit 1; \
	  $(QA_PY) $(QA_DIR)/loop.py --url "http://localhost:$(TILE_QA_PORT)/?tiles=/tiles/tileset.json" $(if $(filter 1,$(JUDGE)),,--no-judge) --workers $(QA_WORKERS) --manifest "$(DIST_MIRROR)/tiles/manifest.json" \
	    --run-id $(or $(QA_RUN),tile-$(shell date +%Y%m%d-%H%M%S))
