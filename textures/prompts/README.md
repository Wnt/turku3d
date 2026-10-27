# prompts/
Codex prompt templates (`string.Template`, `$var` filled by `turkutex/jobs.py` from job `vars`):
`facade_label.md` (strict JSON via `../schemas/facade_label.schema.json`), `facade_improve.md` (one image_gen call),
`facade_generate.md` (one fully generated image_gen elevation), `facade_reference_grid.md` (strict JSON via
`../schemas/facade_reference_grid.schema.json`: is the wall visible, and its measured grid) and `facade_visual_qa.md`
(strict JSON via `../schemas/facade_visual_qa.schema.json`, no image call).
`fragments/` holds the shared style-bible text (`$style`, `$albedo_rules`, `$negatives`; see `../style/STYLE.md`).
Changing a template changes the job content hash, so the runner calls Codex again. Bump `--prompt-version` on purpose.
