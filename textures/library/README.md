# library/
`manifest.jsonl`: one line per accepted texture asset. It records the asset id, the Codex thread_id and job key,
the prompt version, the source and output sha256 values and the data dir of the files. Images and KTX2 files are
stored in `~/turku3d/data/textures/...` and never in git. The pilot entries are written by `make textures-pilot-previews`.

AOI production stages materials under `$TURKU3D_WORK/textures/library/<material_key>/` to avoid
cross-sandbox writes. Run `make textures-library-batch FACADES=/absolute/path/facades.geojson TEX_J=6`.
The orchestrator promotes that tree here at integration. `material.json` records metres per patch,
UV scale, hashes, Codex thread/call provenance, seam/low-frequency checks and glTF KTX2 validation.

`ground.json` defines the eight ground material keys, their physical repeat size and image_gen
prompts. `make ground-jobs ground-library` uses this catalog; full provenance and per-file hashes
are written to `$TURKU3D_WORK/textures/library/MANIFEST.json`. Individual KTX2 sets and the three
shared array textures stay in the sandbox data directory, not git. See
[the ground build instructions](../turkutex/ground/README.md).
The W1-10 run's committed provenance, per-file hashes, memory accounting and QA evidence are
in [docs/reports/ground-textures.json](../../docs/reports/ground-textures.json).
