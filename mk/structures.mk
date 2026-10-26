# Structures are intermediate layers, consumed by the Wave 1 assembler.
STRUCTURES_FIXTURE := $(TURKU3D_WORK)/build/structures-tests/scenes.json

.PHONY: structures-test structures-test-py structures-test-physics structures-build
structures-test-py: ## Fast synthetic structures geometry, lidar, layer and seam tests
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_structures.py
structures-test-physics: $(VALIDATOR)/node_modules/.package-lock.json ## Generated structures through Rapier KCC and 10 m/s rigid-body collision
	@test -n "$(TURKU3D_WORK)" || (echo 'Set TURKU3D_WORK to your sandbox'; exit 1)
	$(PIPELINE_RUN) python -m turku3d.structures.fixtures --out "$(STRUCTURES_FIXTURE)"
	cd $(VALIDATOR) && STRUCTURES_FIXTURE="$(STRUCTURES_FIXTURE)" node --test test/structures.test.ts
	cd $(VALIDATOR) && npx tsc -p tsconfig.json
structures-test: structures-test-py structures-test-physics ## All structures tests (use make -j2)
structures-build: ## Build structures using raw surveys and the completed ground layer
	$(PIPELINE_RUN) python -m turku3d.structures $(ARGS)

.PHONY: bridges-build bridges-test bridges-textures
bridges-textures: ## Stage read-only ground library textures for bridge and scene assembly
	$(PIPELINE_RUN) python -m turku3d.bridges.textures
bridges-build: ## Build mapped bridge decks in the four Phase 1 L0 tiles
	$(PIPELINE_RUN) python -m turku3d.bridges
bridges-test: ## Synthetic bridge geometry and layer contract checks
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_bridges.py
