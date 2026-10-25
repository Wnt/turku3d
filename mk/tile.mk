# Phase 0 exit chain (ARCHITECTURE §4): make tile T=<visual L0 tile id(s)>
#   1 fetch (cached)  -> remote `turku3d.fetch all` for AREA (downloads nothing when raw/ is complete)
#   2 build (remote)  -> turku3d.package.stub_tile: stub ground .col x16 + stub visual GLB per tile, tileset.json,
#                        manifest.json in osgallery-dev:~/turku3d/data/dist/tiles, then a hash check of the tree
#   3 sync            -> tools/dev/sync.sh to $(DIST_MIRROR) (manifest last, sha256 verified)
#   4 validate        -> tools/validator (Node Rapier) on the synced .col tiles, report in $(TILE_REPORTS)
#   5 show            -> game build + Playwright (Chrome, WebGPU and WebGL2) on the synced tiles: tile renders,
#                        character walks on the stub collision, QA render contract incl. ID pass
# The office is in t0_0_0 (E 23459668 N 6704254 -> x 168, z 246). The Phase 1 AOI straddles 4 L0 tiles:
#   make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
# T is shared with col-validate, so it gets no global default here
TILE_IDS       = $(or $(T),t0_0_0)
DIST_MIRROR   ?= $(HOME)/turku3d-data/dist
TILE_REPORTS  ?= $(HOME)/turku3d-data/reports
TILE_WALKERS  ?= 2000
TILE_REMOTE   ?= osgallery-dev
TILE_RENV      = source ~/turku3d/envs/pipeline.env && cd ~/turku3d/repo/pipeline &&

.PHONY: tile tile-fetch tile-build tile-sync tile-validate tile-e2e tile-show tile-test tile-qa

tile: tile-fetch tile-build tile-sync tile-validate tile-e2e ## Phase 0 chain: fetch (cached) -> stub .col+GLB -> tileset/manifest -> sync -> validator -> Chrome (T=t0_0_0)
	@echo "make tile T='$(TILE_IDS)': OK. Play it: make tile-show  (then open the printed URL)"

tile-fetch: pipeline-sync ## Remote fetch of AREA (cached, ~15 s when complete)
	ssh $(TILE_REMOTE) 'bash -lc "$(TILE_RENV) python -m turku3d.fetch all --area $(AREA)"'

tile-build: pipeline-sync ## Remote: build stub tile(s) T into ~/turku3d/data/dist/tiles (+ manifest hash check)
	ssh $(TILE_REMOTE) 'bash -lc "$(TILE_RENV) python -m turku3d.package.stub_tile --area $(AREA) --tile $(TILE_IDS)"'

tile-sync: ## Mirror the remote dist to $(DIST_MIRROR) (sha256-verified)
	LOCAL_DIST=$(DIST_MIRROR) REMOTE_HOST=$(TILE_REMOTE) $(REPO)/tools/dev/sync.sh

tile-validate: col-deps ## Node Rapier validator on the synced collision of T (TILE_WALKERS=2000)
	mkdir -p $(TILE_REPORTS)
	cd $(VALIDATOR) && for t in $(TILE_IDS); do \
	  node src/cli.ts --walkers $(TILE_WALKERS) --out $(TILE_REPORTS)/validate-$$t.json $(DIST_MIRROR)/tiles/collision/$$t || exit 1; done

tile-e2e: game-install ## Build the game + Playwright on the synced tiles (both backends); screenshots in game/test-results/screenshots/tile-*
	cd $(GAME_DIR) && npm run build && TILE_DIST=$(DIST_MIRROR)/tiles npx playwright test -c playwright.tile.config.ts

tile-show: game-build ## Serve game + synced tiles on :8080 and print the URL (Ctrl-C to stop)
	@echo "open http://localhost:8080/?tiles=/tiles/tileset.json   (add &backend=webgl for WebGL2)"
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIR)/dist --port 8080 --mount /tiles=$(DIST_MIRROR)/tiles

tile-test: foundation-venv ## Offline pytest of the packaging/stub-tile code (synthetic inputs)
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m pytest -q tests/test_package.py

TILE_QA_PORT ?= 8091
tile-qa: qa-venv game-build ## Visual QA loop on the synced tiles: render all qa/views (color+id) [JUDGE=1 to call Codex] [QA_RUN=id]
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIR)/dist --port $(TILE_QA_PORT) --mount /tiles=$(DIST_MIRROR)/tiles & pid=$$!; \
	  trap "kill $$pid" EXIT; sleep 1; \
	  $(QA_PY) $(QA_DIR)/loop.py --url "http://localhost:$(TILE_QA_PORT)/?tiles=/tiles/tileset.json" $(if $(JUDGE),,--no-judge) \
	    --run-id $(or $(QA_RUN),tile-$(shell date +%Y%m%d-%H%M%S))
