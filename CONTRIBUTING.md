# Contributing to Turku3D

Thanks for helping improve the streets, gameplay and tooling of Turku3D.

**Start here:** [project overview](README.md) · [development guide](docs/DEVELOPING.md) · [documentation index](docs/README.md)

## Propose a change

1. Check [existing issues](https://github.com/Wnt/turku3d/issues), or open one if the proposed change affects shared formats, user-visible behavior or production operations.
2. Work on a topic branch. Keep changes focused and explain the player or developer problem you are solving.
3. Run **relevant, affected** checks and include the exact commands and results in your PR. For documentation-only changes, review internal links, command spelling, the deployed/local distinction and any behavior claims against source; say when runtime tests were not run.
4. Open a pull request against `main` describing the change, its rationale, verification, limitations and any operational impact. Do not deploy or merge production changes as part of a community PR unless the maintainers explicitly coordinate it.

## Repository conventions

- Use **English** for code, comments, documentation and PR descriptions.
- Prefer the existing TypeScript game, Node.js server and Python pipeline conventions; do not add tooling or dependencies without a concrete need.
- Browser gameplay has a public multiplayer-only variant and a local development variant. Keep both in mind when making UI or documentation changes.
- **Never commit secrets, raw city datasets, generated bulk tiles, non-redistributable street-level imagery, private browser profiles or local caches.** See [AGENTS.md](AGENTS.md), [NOTICE.md](NOTICE.md) and [published image provenance](docs/published-images.toml).
- Report real-city/generator assumptions separately from synthetic-scene test evidence. Historical reports are not guarantees of current production behavior.

The full owner-specific automation, worktree, agent coordination and release procedures are in [AGENTS.md](AGENTS.md) and [scripts/dev/README.md](scripts/dev/README.md). External contributors do not need access to the production data volume, service accounts or deployment secrets to submit useful changes.
