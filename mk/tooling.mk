.PHONY: tooling-test
tooling-test: ## Fast worktree, port, task-runner and landing tests (fake Codex, synthetic repos)
	python3 $(REPO)/scripts/dev/tests/run.py
