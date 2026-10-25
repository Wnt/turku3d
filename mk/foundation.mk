# Foundation targets (Phase 0 step 1): pipeline venv, config tests, generated area manifests.
PIPELINE_VENV ?= $(REPO)/pipeline/.venv
PIPELINE_PY   ?= $(PIPELINE_VENV)/bin/python

.PHONY: foundation-venv foundation-test foundation-areas foundation-where

$(PIPELINE_PY):
	uv venv --python 3.12 $(PIPELINE_VENV)
	uv pip install --python $(PIPELINE_PY) -e '$(REPO)/pipeline[dev]'

foundation-venv: $(PIPELINE_PY) ## Create pipeline/.venv (python 3.12, numpy, pyproj, pytest) with uv

foundation-test: $(PIPELINE_PY) ## Run turku3d.config + spec consistency tests (pytest)
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m pytest -q tests/test_config.py tests/test_specs.py

foundation-areas: $(PIPELINE_PY) ## Regenerate data-manifests/areas/*.yaml from config.py
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.config --area-yaml office-block \
	  > $(REPO)/data-manifests/areas/office-block.yaml

foundation-where: $(PIPELINE_PY) ## Tile ids for a point: make foundation-where E=23459668 N=6704254
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.config --where $(E) $(N)
