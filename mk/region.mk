REGION_WORKERS ?= 4
REGION_QUOTA_CAP ?= 100
.PHONY: region region-test cache-gc
region: ## Build an area resumably; DRY=1 estimates without network or Codex calls
	$(PIPELINE_RUN) python -m turku3d.region --area $(AREA) --workers $(REGION_WORKERS) --quota-cap $(REGION_QUOTA_CAP) $(if $(STEPS),--steps $(STEPS)) $(if $(filter 1,$(DRY)),--dry-run)
region-test: ## Offline synthetic region, resume, quota and cache tests
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q tests/test_region.py
cache-gc: ## Report shared streetlevel cache size; APPLY=1 removes old partial downloads only
	$(PIPELINE_RUN) python -m turku3d.region.cache $(if $(filter 1,$(APPLY)),--apply)
