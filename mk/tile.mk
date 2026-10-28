# Local chain, deliberately ordered even under make -j.
TILE_IDS = $(or $(T),t0_0_0)
DIST_MIRROR ?= $(TURKU3D_WORK)/dist
TILE_REPORTS ?= $(TURKU3D_WORK)/reports
TILE_WALKERS ?= 2000
comma := ,
VALIDATOR_REQUIRE ?= buildings,curbs,cars$(if $(wildcard $(TURKU3D_WORK)/build/layers/structures/MANIFEST.json),$(comma)stairs,)

.PHONY: tile tile-reuse tile-fetch tile-build tile-validate tile-e2e tile-show tile-test tile-qa tiles-refresh-stale
tile: ## Assemble changed tiles and check format/seams; focused behavior evidence is separate
	$(MAKE) tile-build
	$(MAKE) tile-validate VALIDATOR_REQUIRE=format,seams 'VALIDATOR_ARGS=--checks format,seams'

tile-reuse: ## Clone missing layers, textures and built tiles from one frozen live release
	python3 $(REPO)/scripts/dev/reuse_tiles.py

tile-fetch: ## Check cached work-3x3 inputs without network or writes to shared raw
	$(PIPELINE_RUN) python -m turku3d.fetch.cached --area work-3x3

tile-build: ## Build T into TURKU3D_WORK/dist/tiles and verify hashes and seams; PUT=1 streams changed tiles to the localhost game
	python3 $(REPO)/scripts/dev/host_slots.py $(if $(word 2,$(TILE_IDS)),build,check) -- $(PIPELINE_RUN) python -m turku3d.package.assemble --area $(AREA) --tile $(TILE_IDS)
	$(if $(filter 1,$(PUT)),python3 $(REPO)/scripts/dev/tile_store.py put-tiles --repo $(REPO) --tiles-dir $(TURKU3D_WORK)/dist/tiles --tiles $(TILE_IDS))

tiles-refresh-stale: ## Start the data lane on the live tiles built with older tile code than main's (sentinel heartbeat, load < 40)
	python3 $(REPO)/scripts/dev/integrator.py refresh-stale

tile-validate: col-deps ## Node Rapier validation (TILE_WALKERS=2000); verdicts go into the tile quality ledger
	mkdir -p $(TILE_REPORTS)
	rm -f $(TILE_REPORTS)/validate-aoi.json
	cd $(VALIDATOR) && node src/cli.ts --walkers $(TILE_WALKERS) --workers 4 \
	  --require $(VALIDATOR_REQUIRE) --out $(TILE_REPORTS)/validate-aoi.json \
	  $(if $(wildcard $(TURKU3D_WORK)/build/layers),--layers $(TURKU3D_WORK)/build/layers) \
	  $(VALIDATOR_ARGS) $(or $(VALIDATOR_INPUTS),$(foreach tile,$(TILE_IDS),$(DIST_MIRROR)/tiles/collision/$(tile))); \
	  status=$$?; test ! -f $(TILE_REPORTS)/validate-aoi.json || $(PIPELINE_RUN) \
	  python -m turku3d.region quality --dist $(DIST_MIRROR)/tiles --validated $(TILE_REPORTS)/validate-aoi.json; exit $$status

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
