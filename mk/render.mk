# Rendering unit gate and hardware acceptance tests. Ports are explicitly overridable per sandbox.
.PHONY: render-test render-e2e render-perf

render-test: game-install ## Render typecheck + synthetic unit tests (no browser or real tiles)
	cd $(GAME_DIR) && npm run typecheck && npm test -- tests/unit/render.test.ts

render-e2e: game-install ## All render tiers, river/ID contract and 1440p perf on Chrome/Metal (E2E_PORT)
	cd $(GAME_DIR) && npm run test:render

render-perf: game-build ## Fixed-path 1440p CPU/GPU/frame budgets, per-commit JSON (PERF_TILES, PERF_QUALITY, E2E_PORT)
	cd $(GAME_DIR) && npx playwright test -c playwright.render.config.ts performance.spec.ts
