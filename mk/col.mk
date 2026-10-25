# .col collision format (pipeline/turku3d/col, game/src/shared/col) and the Node Rapier validator (tools/validator).
PIPELINE_VENV ?= $(REPO)/pipeline/.venv
PIPELINE_PY   ?= $(PIPELINE_VENV)/bin/python
VALIDATOR     := $(REPO)/tools/validator
COL_RT_DIR    ?= $(TURKU3D_WORK)/build/col-roundtrip

.PHONY: col-deps col-materials col-fixtures col-test col-test-py col-test-ts col-typecheck col-validate col-validate-fixtures

$(VALIDATOR)/node_modules/.package-lock.json: $(VALIDATOR)/package.json $(VALIDATOR)/package-lock.json
	cd $(VALIDATOR) && npm ci --no-audit --no-fund

col-deps: foundation-venv $(VALIDATOR)/node_modules/.package-lock.json ## Install validator npm deps (Rapier compat 0.21, meshoptimizer 1.3, tsc) + pipeline venv

col-materials: foundation-venv ## Regenerate materials_v1.json + game/src/shared/col/materials.ts from docs/specs/materials.md
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.col.materials --write

col-fixtures: col-deps ## Regenerate the synthetic validator fixtures in tools/validator/fixtures
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.col.fixtures --out $(VALIDATOR)/fixtures

col-test-py: col-deps ## Python writer/reader/codec/fixture tests (pytest)
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m pytest -q turku3d/col/tests

col-typecheck: col-deps ## tsc --noEmit over game/src/shared/col + tools/validator
	cd $(VALIDATOR) && npx tsc -p tsconfig.json

col-test-ts: col-deps col-typecheck ## Python->TS round trip, KCC behaviour and fixture pass/fail tests (node --test)
	rm -rf $(COL_RT_DIR)
	cd $(REPO)/pipeline && $(PIPELINE_PY) -m turku3d.col.roundtrip --out $(COL_RT_DIR)
	cd $(VALIDATOR) && COL_RT_DIR=$(COL_RT_DIR) node --test --test-concurrency=1 test/roundtrip.test.ts test/kcc.test.ts test/fixtures.test.ts

col-test: col-test-py col-test-ts ## All .col + validator tests

col-validate: col-deps ## Validate tiles: make col-validate T=<dir|files> [FOOTPRINTS=f.geojson] [CURBS=f.geojson] [OUT=report.json] [ARGS=...]
	cd $(VALIDATOR) && node src/cli.ts $(if $(FOOTPRINTS),--footprints $(FOOTPRINTS)) $(if $(CURBS),--curbs $(CURBS)) \
	  $(if $(OUT),--out $(OUT)) $(ARGS) $(T)

col-validate-fixtures: col-deps ## Run the validator CLI on fixtures/good (expect PASS) with the full 2000 walkers
	cd $(VALIDATOR) && node src/cli.ts --footprints fixtures/good/footprints.geojson fixtures/good
