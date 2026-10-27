# Root Makefile. Each work area adds its targets in its own mk/<area>.mk file.
# Document a target by putting "## description" after its name, e.g.
#   foo-build: ## Build foo
# and it shows up in `make help`.
.DEFAULT_GOAL := help
REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
export REPO
TURKU3D_JOBS ?= $(shell python3 $(REPO)/scripts/dev/resources.py)
export TURKU3D_JOBS
ifeq ($(MAKELEVEL),0)
MAKEFLAGS += -j$(TURKU3D_JOBS)
endif

# Shared inputs; sandbox-specific outputs and services (source sandbox.env in a worktree).
TURKU3D_DATA ?= $(HOME)/turku3d/data
TURKU3D_WORK ?= $(TURKU3D_DATA)
TURKU3D_SANDBOX ?= main
TURKU3D_PORT_BASE ?= 5170
TURKU3D_BROWSER_DIR ?= $(HOME)/turku3d/browser/$(TURKU3D_SANDBOX)
export TURKU3D_DATA TURKU3D_WORK TURKU3D_SANDBOX TURKU3D_PORT_BASE TURKU3D_BROWSER_DIR
GAME_DIST := $(TURKU3D_WORK)/build/game
DIST_PORT := $(shell expr $(TURKU3D_PORT_BASE) + 1)
QA_PORT := $(shell expr $(TURKU3D_PORT_BASE) + 2)
export npm_config_cache := $(TURKU3D_WORK)/cache/npm
export UV_CACHE_DIR := $(TURKU3D_WORK)/cache/uv

include $(sort $(wildcard $(REPO)/mk/*.mk))

.PHONY: help
help: ## List all documented targets
	@grep -hE '^[a-zA-Z0-9_./%-]+:.*## ' $(MAKEFILE_LIST) \
	  | sort -u \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
