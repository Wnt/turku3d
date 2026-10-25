# prompts/
Codex prompt templates (`string.Template`, `$var` filled by `turkutex/jobs.py` from job `vars`):
`facade_label.md` (strict JSON via `../schemas/facade_label.schema.json`), `facade_improve.md` (one image_gen call).
`fragments/` holds the shared style-bible text (`$style`, `$albedo_rules`, `$negatives`; see `../style/STYLE.md`).
Changing a template changes the job content hash, so the runner calls Codex again. Bump `--prompt-version` on purpose.
