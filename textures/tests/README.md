# tests/
`make textures-test` (or `cd textures && .venv/bin/python -m pytest -q`). `fake_codex.py` imitates `codex exec --json`
and `codex app-server`: rollouts, generated_images and the quota. Runner tests therefore need no network or quota.
The KTX2 and DeepBump tests are skipped when node_modules or DeepBump are missing.

AOI regressions in `test_facade_batch.py` cover building grouping, rejected references, geometric
counts/heights, path/index validation, wall-plane rectification, read-only source caches and missing
contact-sheet entries. Runner tests also reject schema-invalid JSON and repair deleted image outputs.
`make textures-test` runs three pytest workers. KTX integration uses a 128-pixel synthetic image and
requires the sandbox-native tools (`make textures-ktx`); it validates all maps for KHR_texture_basisu.
