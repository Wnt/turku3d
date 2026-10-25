# Game skeleton (game/) and dev tools (tools/dev/). See game/README.md.
GAME_DIR := $(REPO)/game
GAME_NPM := cd $(GAME_DIR) && npm
DIST_MIRROR ?= $(or $(TURKU3D_WORK),$(HOME)/turku3d/data)/dist

.PHONY: game-install game-dev game-build game-test game-e2e game-test-all game-dev-tiles game-preview game-serve-dist game-dev-tools-test

$(GAME_DIR)/node_modules/.package-lock.json: $(GAME_DIR)/package.json
	$(GAME_NPM) install
	@touch $@

game-install: $(GAME_DIR)/node_modules/.package-lock.json ## npm install for game/

game-dev-tiles: game-install ## Regenerate the dummy tileset game/public/dev-tiles (GLB + .col + manifest)
	$(GAME_NPM) run dev-tiles

game-dev: game-install ## Vite dev server http://localhost:5173 (COOP/COEP); ?backend=webgl for WebGL2
	$(GAME_NPM) run dev

game-build: game-install ## Typecheck + production build into game/dist
	$(GAME_NPM) run build

game-test: game-install ## Node unit tests (vitest: .col reader, Rapier character/car on the dev tiles, origin, sun)
	$(GAME_NPM) test

game-e2e: game-install ## Build + Playwright smoke test in Chrome (WebGPU and WebGL2), screenshots in game/test-results/screenshots
	$(GAME_NPM) run test:e2e

game-test-all: game-test game-e2e game-dev-tools-test ## All game + tools/dev tests

game-preview: game-build ## Serve game/dist with tools/dev/serve-dist.mjs on :8080 (+ /tiles -> $(DIST_MIRROR)/tiles)
	node $(REPO)/tools/dev/serve-dist.mjs --root $(GAME_DIR)/dist --port 8080 --mount /tiles=$(DIST_MIRROR)/tiles

game-serve-dist: ## Serve the mirrored tile dist ($(DIST_MIRROR)) on :8081
	node $(REPO)/tools/dev/serve-dist.mjs --root $(DIST_MIRROR) --port 8081



game-dev-tools-test: ## node --test for tools/dev/serve-dist.mjs
	node --test $(REPO)/tools/dev/serve-dist.test.mjs
