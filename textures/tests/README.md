# tests/
`make textures-test` (or `cd textures && .venv/bin/python -m pytest -q`). `fake_codex.py` imitates `codex exec --json`
and `codex app-server`: rollouts, generated_images and the quota. Runner tests therefore need no network or quota.
The KTX2 and DeepBump tests are skipped when node_modules or DeepBump are missing.
