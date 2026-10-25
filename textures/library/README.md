# library/
`manifest.jsonl`: one line per accepted texture asset. It records the asset id, the Codex thread_id and job key,
the prompt version, the source and output sha256 values and the data dir of the files. Images and KTX2 files are
stored in `~/turku3d/data/textures/...` and never in git. The pilot entries are written by `make textures-pilot-previews`.
