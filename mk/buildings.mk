.PHONY: buildings-test buildings-test-py buildings-test-physics buildings-build buildings-validate
buildings-test: buildings-test-py buildings-test-physics ## Fast synthetic shell, UV, seam and Rapier tests
buildings-test-py:
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_buildings.py
buildings-test-physics:
	cd $(REPO)/tools/validator && npm run typecheck && node --test test/buildings.test.ts
buildings-build: ## Reconstruct and compare the city/roofer buildings layer
	$(PIPELINE_RUN) python -m turku3d.buildings
buildings-validate: ## Run 2,000 Rapier walkers against the exported buildings layer
	$(PIPELINE_RUN) python -m turku3d.buildings.validate
