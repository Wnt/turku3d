# Root Makefile. Each work area adds its targets in its own mk/<area>.mk file.
# Document a target by putting "## description" after its name, e.g.
#   foo-build: ## Build foo
# and it shows up in `make help`.
.DEFAULT_GOAL := help
REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
export REPO

include $(sort $(wildcard $(REPO)/mk/*.mk))

.PHONY: help
help: ## List all documented targets
	@grep -hE '^[a-zA-Z0-9_./%-]+:.*## ' $(MAKEFILE_LIST) \
	  | sort -u \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
