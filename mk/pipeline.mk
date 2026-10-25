# Remote pipeline (workstream "remote-pipeline"): env bootstrap, repo sync, fetchers.
# The code lives in this repo on the Mac; `pipeline-sync` rsyncs it to $(REMOTE):~/turku3d/repo/ and the
# other remote targets run there over ssh inside ~/turku3d/envs/pipeline.
REMOTE      ?= osgallery-dev
REMOTE_REPO ?= turku3d/repo
AREA        ?= office-block
# Command prefix on the remote: activate the env, cd into the synced pipeline.
RENV         = source ~/turku3d/envs/pipeline.env && cd ~/$(REMOTE_REPO)/pipeline &&
RSYNC_EXCL   = --exclude .venv --exclude __pycache__ --exclude '*.egg-info' --exclude .pytest_cache \
               --exclude node_modules --exclude '/data/'

.PHONY: pipeline-venv pipeline-sync pipeline-bootstrap pipeline-bootstrap-update pipeline-versions pipeline-ssh-run \
        pipeline-test pipeline-test-remote pipeline-sources fetch-laz fetch-wfs fetch-wms fetch-osm \
        fetch-aoi fetch-report fetch-checksums

pipeline-sync: ## rsync pipeline/, data-manifests/, docs/specs/, ARCHITECTURE.md to $(REMOTE):~/turku3d/repo/
	ssh $(REMOTE) 'mkdir -p ~/$(REMOTE_REPO)'
	rsync -az --delete $(RSYNC_EXCL) $(REPO)/pipeline $(REPO)/data-manifests $(REMOTE):$(REMOTE_REPO)/
	ssh $(REMOTE) 'mkdir -p ~/$(REMOTE_REPO)/docs' && rsync -az --delete $(REPO)/docs/specs $(REMOTE):$(REMOTE_REPO)/docs/
	rsync -az $(REPO)/docs/ARCHITECTURE.md $(REMOTE):$(REMOTE_REPO)/docs/

pipeline-bootstrap: pipeline-sync ## Create remote env ~/turku3d/envs/pipeline + node tools + roofer (idempotent)
	ssh $(REMOTE) 'bash ~/$(REMOTE_REPO)/pipeline/env/bootstrap-remote.sh'

pipeline-bootstrap-update: pipeline-sync ## Same, but also re-solve the env against environment.yml
	ssh $(REMOTE) 'bash ~/$(REMOTE_REPO)/pipeline/env/bootstrap-remote.sh --update'

pipeline-versions: pipeline-sync ## Print remote tool/library versions (fails if something is missing)
	ssh $(REMOTE) 'bash ~/$(REMOTE_REPO)/pipeline/env/versions.sh'

pipeline-ssh-run: pipeline-sync ## Run CMD in the remote env: make pipeline-ssh-run CMD='python -m turku3d.config'
	ssh $(REMOTE) 'bash -lc "$(RENV) $(CMD)"'

PIPELINE_FETCH_STAMP = $(PIPELINE_VENV)/.fetch-extras

$(PIPELINE_FETCH_STAMP): $(PIPELINE_PY) $(REPO)/pipeline/pyproject.toml
	uv pip install --python $(PIPELINE_PY) -e '$(REPO)/pipeline[dev,fetch]'
	touch $@

pipeline-venv: $(PIPELINE_FETCH_STAMP) ## Local pipeline/.venv with the [fetch] extras (geopandas, rasterio, laspy...)

pipeline-test: $(PIPELINE_FETCH_STAMP) ## Local pytest of the whole pipeline package (gdal/osmium tests skip on the Mac)
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m pytest -q

pipeline-test-remote: pipeline-sync ## pytest in the remote conda env (full: geopandas, rasterio, osmium)
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m pytest -q"'

pipeline-sources: $(PIPELINE_PY) ## Regenerate data-manifests/sources.yaml from turku3d/fetch/sources.py
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.fetch.sources > $(REPO)/data-manifests/sources.yaml

fetch-laz: pipeline-sync ## Remote: fetch LAZ tiles (pistepilvi+maanpinta) for AREA
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch laz --area $(AREA)"'

fetch-wfs: pipeline-sync ## Remote: fetch all WFS layers for AREA (quadtree-paged GML -> GeoParquet)
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch wfs --area $(AREA)"'

fetch-wms: pipeline-sync ## Remote: fetch WMS orthos (2022 TO, 2025, drone) for AREA as tiled GeoTIFF + VRT
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch wms --area $(AREA)"'

fetch-osm: pipeline-sync ## Remote: Geofabrik Finland pbf + osmium clip for AREA (+ GeoParquet)
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch osm --area $(AREA)"'

fetch-aoi: pipeline-sync ## Remote: all fetchers for AREA, then the sanity report
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch all --area $(AREA)"'

fetch-report: pipeline-sync ## Remote: sanity report (points/classes, WFS counts, ortho resolution) -> raw/REPORT-<area>.json
	ssh $(REMOTE) 'bash -lc "$(RENV) python -m turku3d.fetch report --area $(AREA)"'

fetch-checksums: ## Pull remote raw SHA256SUMS files into data-manifests/checksums/
	mkdir -p $(REPO)/data-manifests/checksums
	for k in laz wfs ortho osm; do \
	  rsync -az $(REMOTE):turku3d/data/raw/$$k/SHA256SUMS $(REPO)/data-manifests/checksums/$$k.sha256 || true; done
	rsync -az $(REMOTE):'turku3d/data/raw/REPORT-*.json' $(REPO)/data-manifests/checksums/ || true
