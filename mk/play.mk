# Cached playable entry point and evidence-producing routes. See game/README.md.
.PHONY: play play-prepare playtest playtest-test
play: ## Build/reuse the AOI + game, then serve on TURKU3D_PORT_BASE (main default 5170)
	node $(REPO)/tools/dev/play.mjs play

play-prepare: ## Prepare cached AOI tiles and production game without starting a server
	node $(REPO)/tools/dev/play.mjs prepare

playtest: ## Run virtual-Xbox routes on both backends; write HTML, videos and screenshots
	node $(REPO)/tools/dev/play.mjs test $(PLAYTEST_ARGS)

playtest-test: game-install ## Typecheck + fast offline cache/report tests
	$(GAME_NPM) run typecheck
	node --test $(REPO)/tools/dev/play.test.mjs
