# library/
`manifest.jsonl`: one line per accepted texture asset. It records the asset id, the Codex thread_id and job key,
the prompt version, the source and output sha256 values and the data dir of the files. Images and KTX2 files are
stored in `~/turku3d/data/textures/...` and never in git. The pilot entries are written by `make textures-pilot-previews`.

AOI production stages materials under `$TURKU3D_WORK/textures/library/<material_key>/` to avoid
cross-sandbox writes. Run `make textures-library-batch FACADES=/absolute/path/facades.geojson TEX_J=6`.
The orchestrator promotes that tree here at integration. `material.json` records metres per patch,
UV scale, hashes, Codex thread/call provenance, seam/low-frequency checks and glTF KTX2 validation.
