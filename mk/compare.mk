# Local-only Google reference captures. See tools/compare/README.md.
GMAPS_PY ?= $(REPO)/.venv/bin/python
GMAPS_ARGS ?=

.PHONY: gmaps-compare
gmaps-compare: ## Local game / Google aerial pair: CAM=E,N,H,yaw,pitch,fov GMAPS_ARGS='--height-datum ... --height-offset ...'
	$(GMAPS_PY) $(REPO)/tools/compare/gmaps_compare.py $(if $(CAM),--cam "$(CAM)") $(GMAPS_ARGS)
