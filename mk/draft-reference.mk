.PHONY: draft-reference-score
draft-reference-score: ## Private paired draft metrics: BEFORE=<ref> AFTER=<ref|worktree|path>
	@python3 $(REPO)/scripts/dev/draft_reference_score.py --before "$(BEFORE)" --after "$(AFTER)" $(if $(T),--tiles $(T)) $(if $(CONTEXT_TILES),--context-tiles $(CONTEXT_TILES))
