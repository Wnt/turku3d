.PHONY: tooling-test gate-deep
tooling-test: ## Fast worktree, port, task-runner and landing tests (fake Codex, synthetic repos)
	python3 $(REPO)/scripts/dev/tests/run.py

gate-deep: ## Deep gate tier (slow test stages) for changes since BASE (default main); ALL=1 runs every deep stage
	$(REPO)/scripts/dev/gate --deep $(if $(ALL),--all,--base $(or $(BASE),main))
