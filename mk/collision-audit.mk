.PHONY: collision-audit collision-sweep collision-audit-test playtest

playtest: collision-sweep

collision-audit: ## Compare published layer collision and visuals within 40 m of spawn
	$(PIPELINE_RUN) python -m turku3d.collision_audit

collision-sweep: game-install ## Sweep the published spawn through the game physics world
	cd $(GAME_DIR) && COLLISION_SWEEP_DIST=$(TURKU3D_WORK)/dist/tiles \
	  COLLISION_SWEEP_REPORT=$(TURKU3D_WORK)/reports/collision-audit/sweep.json \
	  npm exec -- vitest run tests/unit/collision-sweep.test.ts
	$(PIPELINE_RUN) python -m turku3d.collision_audit \
	  --sweep $(TURKU3D_WORK)/reports/collision-audit/sweep.json

collision-audit-test: ## Synthetic collider/visual regression fixture
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_collision_audit.py
