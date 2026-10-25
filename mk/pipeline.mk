# Native macOS pipeline. Shared raw inputs, sandbox-local products.
AREA ?= office-block
PIPELINE_RUN = bash $(REPO)/pipeline/env/run.sh

.PHONY: pipeline-bootstrap pipeline-bootstrap-update pipeline-versions pipeline-test pipeline-venv pipeline-sources fetch-aoi fetch-report fetch-checksums
pipeline-bootstrap: ## Install native osx-arm64 tools under ~/turku3d (idempotent)
	bash $(REPO)/pipeline/env/bootstrap.sh
pipeline-bootstrap-update: ## Re-solve the native environment
	bash $(REPO)/pipeline/env/bootstrap.sh --update
pipeline-versions: ## Check native pipeline tools and Python libraries
	bash $(REPO)/pipeline/env/versions.sh
pipeline-venv: pipeline-bootstrap ## Install the complete local pipeline environment
pipeline-test: ## Offline pipeline tests in the complete native environment
	cd $(REPO)/pipeline && $(PIPELINE_RUN) python -m pytest -q -n $(TURKU3D_JOBS)
pipeline-sources: ## Regenerate the data source manifest
	$(PIPELINE_RUN) python -m turku3d.fetch.sources > $(REPO)/data-manifests/sources.yaml
fetch-laz fetch-wfs fetch-wms fetch-osm: ## Fetch the named input kind locally for AREA
	$(PIPELINE_RUN) python -m turku3d.fetch $(patsubst fetch-%,%,$@) --area $(AREA)
fetch-aoi: ## Fetch every input for AREA and write its report
	$(PIPELINE_RUN) python -m turku3d.fetch all --area $(AREA)
fetch-report: ## Write the input report for AREA
	$(PIPELINE_RUN) python -m turku3d.fetch report --area $(AREA)
fetch-checksums: ## Export raw checksums and reports to data-manifests/checksums
	$(PIPELINE_RUN) python -m turku3d.fetch.export --area $(AREA)
