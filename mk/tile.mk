# Local chain, deliberately ordered even under make -j.
TILE_IDS = $(or $(T),t0_0_0)
TURKU3D_DATA ?= $(HOME)/turku3d/data
TURKU3D_WORK ?= $(TURKU3D_DATA)
TURKU3D_PORT_BASE ?= 5170
DIST_MIRROR ?= $(TURKU3D_WORK)/dist
TILE_REPORTS ?= $(TURKU3D_WORK)/reports
TILE_WALKERS ?= 2000
TILE_PORT = $(shell expr $(TURKU3D_PORT_BASE) + 1)

.PHONY: tile tile-fetch tile-build tile-validate tile-e2e tile-show tile-test tile-qa
tile: ## Fetch cached inputs, build, verify seams, validate physics and run Chrome e2e locally
	$(MAKE) tile-fetch
	$(MAKE) tile-build
	$(MAKE) tile-validate
	$(MAKE) tile-e2e

tile-fetch: ## Check cached work-3x3 inputs without network or writes to shared raw
	$(PIPELINE_RUN) python -m turku3d.fetch.cached --area work-3x3

tile-build: ## Build T into TURKU3D_WORK/dist/tiles and verify hashes and seams
	$(PIPELINE_RUN) python -m turku3d.package.stub_tile --area work-3x3 --tile $(TILE_IDS)

tile-validate: col-deps ## Node Rapier validation (TILE_WALKERS=2000)
	mkdir -p $(TILE_REPORTS)
	cd $(VALIDATOR) && for t in $(TILE_IDS); do \
	  node src/cli.ts --walkers $(TILE_WALKERS) --out $(TILE_REPORTS)/validate-$$t.json $(DIST_MIRROR)/tiles/collision/$$t || exit 1; done

tile-e2e: game-install ## Build the game and test real tiles with Chrome WebGPU and WebGL2
	cd $(GAME_DIR) && npm run build && E2E_TILE_PORT=$(TILE_PORT) TILE_DIST=$(DIST_MIRROR)/tiles npx playwright test -c playwright.tile.config.ts

tile-show: game-build ## Serve the local result on the sandbox dist port
	@echo "open http://localhost:$(TILE_PORT)/?tiles=/tiles/tileset.json"
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIR)/dist --port $(TILE_PORT) --mount /tiles=$(DIST_MIRROR)/tiles

tile-test: ## Offline packaging tests with synthetic inputs
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q -n 4 tests/test_package.py

tile-qa: qa-venv game-build ## Render the reference views on local tiles (JUDGE=1 enables judging)
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIR)/dist --port $(TILE_PORT) --mount /tiles=$(DIST_MIRROR)/tiles & pid=$$!; \
	  trap "kill $$pid" EXIT; sleep 1; \
	  $(QA_PY) $(QA_DIR)/loop.py --url "http://localhost:$(TILE_PORT)/?tiles=/tiles/tileset.json" $(if $(JUDGE),,--no-judge) \
	    --run-id $(or $(QA_RUN),tile-$(shell date +%Y%m%d-%H%M%S))
