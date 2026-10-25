# Visual QA loop (ARCHITECTURE D8): reference views, game renders, Codex GPT-6-Astra judge, reports.
# See qa/README.md. Codex calls are quota-guarded (QA_CODEX_CAP, default 100 % weekly usage).
QA_DIR    := $(REPO)/qa
QA_VENV   ?= $(QA_DIR)/.venv
QA_PY     ?= $(QA_VENV)/bin/python
QA_DATA   ?= $(or $(TURKU3D_WORK),$(TURKU3D_DATA),$(HOME)/turku3d/data)/qa
export QA_DATA
QA_URL    ?= http://localhost:5173/
QA_WORKERS ?= 3
QA_MARGIN ?= 0.5
QA_RUN    ?=

.PHONY: qa-venv qa-test qa-test-py qa-test-render qa-refs qa-views-search qa-render-stub qa-loop qa-loop-stub \
        qa-selftest qa-report qa-usage

$(QA_PY):
	uv venv -q -p 3.12 $(QA_VENV)
	uv pip install -q -p $(QA_PY) --no-deps streetlevel==0.12.13
	uv pip install -q -p $(QA_PY) aiohttp numpy pillow 'protobuf>4' pycryptodome requests scipy pyproj \
	  bd09convertor coordinatesconverter pyequilib jsonschema pytest opencv-python-headless laspy lazrs
	SP=$$($(QA_PY) -c "import site;print(site.getsitepackages()[0])"); mkdir -p $$SP/pyexiv2 && \
	  printf 'class Image: pass\nclass ImageData: pass\n' > $$SP/pyexiv2/__init__.py

$(QA_DIR)/render/node_modules:
	cd $(QA_DIR)/render && npm install --silent

qa-venv: $(QA_PY) $(QA_DIR)/render/node_modules ## Create qa/.venv (py3.12 + streetlevel) and qa/render node_modules (playwright)

qa-test: qa-test-py qa-test-render ## Run all QA tooling tests (pytest + node contract tests vs the stub); no network, no Codex

qa-test-py: $(QA_PY)
	cd $(QA_DIR) && $(QA_PY) -m pytest -q

qa-test-render: $(QA_DIR)/render/node_modules
	cd $(QA_DIR)/render && npm test --silent

qa-refs: $(QA_PY) ## Re-create missing reference images (data/qa/refs) from qa/views/views.jsonl + selftest registry
	$(QA_PY) $(QA_DIR)/views/sv_views.py refetch --force
	$(QA_PY) $(QA_DIR)/views/sv_views.py --registry $(QA_DIR)/selftest/other_views.jsonl refetch --force

qa-views-search: $(QA_PY) ## List Street View panos near a point: make qa-views-search E=23459668 N=6704254
	$(QA_PY) $(QA_DIR)/views/sv_views.py search --near $(E),$(N) --half 60

qa-render-stub: $(QA_DIR)/render/node_modules ## Render all registered views with the contract stub into data/qa/stub-renders
	cd $(QA_DIR)/render && node render.mjs --views $(QA_DIR)/views/views.jsonl --serve stub --out $(QA_DATA)/stub-renders

qa-loop: $(QA_PY) $(QA_DIR)/render/node_modules ## Render+judge+report against the running game: make qa-loop QA_URL=http://localhost:5173/
	$(QA_PY) $(QA_DIR)/loop.py --url "$(QA_URL)" --workers $(QA_WORKERS) $(if $(QA_RUN),--run-id $(QA_RUN))

qa-loop-stub: $(QA_PY) $(QA_DIR)/render/node_modules ## Plumbing check of the loop with the stub page, no Codex calls
	$(QA_PY) $(QA_DIR)/loop.py --serve stub --no-judge --run-id stub-plumbing

qa-selftest: $(QA_PY) qa-refs ## Planted-difference judge test (9 Codex calls; skips pairs already judged)
	$(QA_PY) $(QA_DIR)/selftest/planted.py prepare
	$(QA_PY) $(QA_DIR)/selftest/planted.py judge
	$(QA_PY) $(QA_DIR)/selftest/planted.py eval

qa-report: $(QA_PY) ## Rebuild issues.jsonl + report.html of one run: make qa-report QA_RUN=<run_id>
	$(QA_PY) $(QA_DIR)/report/report.py $(QA_DATA)/runs/$(QA_RUN)

qa-usage: ## Show Codex weekly usage (no model call)
	python3 $(QA_DIR)/judge/codex_usage.py

qa-regress: $(QA_PY) ## Compare complete runs: QA_BASELINE=<id> QA_RUN=<id> QA_MARGIN=0.5
	$(QA_PY) $(QA_DIR)/regress.py --baseline "$(QA_BASELINE)" --current "$(QA_RUN)" --margin $(QA_MARGIN)

qa-calibrate: $(QA_PY) ## Three independent judgments of one pair: QA_RUN=<id> QA_VIEW=<id>
	$(QA_PY) $(QA_DIR)/calibrate.py --run "$(QA_DATA)/runs/$(QA_RUN)" --view "$(QA_VIEW)"
