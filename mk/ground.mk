.PHONY: ground-build ground-test
ground-build: ## Build and QA the four Phase 1 ground-layer visual tiles (sandbox-local)
	$(PIPELINE_RUN) python -m turku3d.ground.build $(GROUND_ARGS)
ground-test: ## Fast synthetic ground selection, curb, CDT, water and seam tests
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_ground.py
