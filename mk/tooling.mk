.PHONY: tooling-test
tooling-test: ## Fast worktree, port, task-runner and landing tests (fake Codex, synthetic repos)
	python3 $(REPO)/scripts/dev/tests/run.py

.PHONY: engine-probe-test
$(REPO)/tools/engine-probe/node_modules/.package-lock.json: $(REPO)/tools/engine-probe/package.json $(REPO)/tools/engine-probe/package-lock.json
	cd $(REPO)/tools/engine-probe && npm ci
	@touch $@

engine-probe-test: $(REPO)/tools/engine-probe/node_modules/.package-lock.json ## Build isolated engine probes and verify their synthetic physics scene
	cd $(REPO)/tools/engine-probe && npm run build && npm run test:node
