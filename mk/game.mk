# Game skeleton (game/) and dev tools (tools/dev/). See game/README.md.
GAME_DIR := $(REPO)/game
GAME_NPM := cd $(GAME_DIR) && npm
DIST_MIRROR ?= $(TURKU3D_WORK)/dist

.PHONY: game-install game-dev game-build game-test game-e2e game-test-all game-dev-tiles game-preview game-serve-dist game-dev-tools-test

$(GAME_DIR)/node_modules/.package-lock.json: $(GAME_DIR)/package.json
	$(GAME_NPM) install
	@touch $@

game-install: $(GAME_DIR)/node_modules/.package-lock.json ## npm install for game/

game-dev-tiles: game-install ## Regenerate the dummy tileset game/public/dev-tiles (GLB + .col + manifest)
	$(GAME_NPM) run dev-tiles

# Both local servers start the coarse tile service beside them (port base + 5) and stop it on exit.
COARSE_SERVICE = $(REPO)/tools/dev/coarse-tiles & coarse=$$!; trap 'kill $$coarse' EXIT INT TERM;

game-dev: game-install ## Vite dev server http://localhost:$(TURKU3D_PORT_BASE) (COOP/COEP) + coarse tile service; ?backend=webgl for WebGL2
	$(COARSE_SERVICE) $(GAME_NPM) run dev

game-build: game-install ## Typecheck + production build into $(GAME_DIST)
	cd $(GAME_DIR) && python3 $(REPO)/scripts/dev/host_slots.py build -- node scripts/build.mjs

game-test: game-install ## Node unit tests (vitest: .col reader, Rapier character/car on the dev tiles, origin, sun)
	$(GAME_NPM) test

game-e2e: game-install ## Build + Playwright smoke test in Chrome (WebGPU and WebGL2), screenshots in $(TURKU3D_WORK)/reports/screenshots
	$(MAKE) game-build
	cd $(GAME_DIR) && python3 $(REPO)/scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test

game-test-all: game-test game-e2e game-dev-tools-test ## All game + tools/dev tests

game-preview: game-build ## Serve $(GAME_DIST) with tools/dev/serve-dist.mjs on the sandbox dist port (+ /tiles -> $(DIST_MIRROR)/tiles, /coarse -> coarse tile service)
	$(COARSE_SERVICE) node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIST) --port $(DIST_PORT) --mount /tiles=$(DIST_MIRROR)/tiles

game-serve-dist: ## Serve the local tile dist ($(DIST_MIRROR)) on the sandbox dist port
	node $(REPO)/tools/dev/serve-dist.mjs --root $(DIST_MIRROR) --port $(DIST_PORT)



game-dev-tools-test: ## node --test for tools/dev/serve-dist.mjs
	node --test $(REPO)/tools/dev/serve-dist.test.mjs

.PHONY: game-vehicle-test
game-vehicle-test: game-install ## Car acceptance on small synthetic fixtures (game WASM)
	$(GAME_NPM) exec -- vitest run tests/unit/vehicle.test.ts tests/unit/vehicle-world.test.ts
