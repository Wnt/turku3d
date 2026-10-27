# schemas/
JSON Schemas for `codex exec --output-schema` (strict: every property required, `additionalProperties: false`,
enums). `facade_label.schema.json` v1: facade spec for the procedural facade generator and for picking usable views.
Bay counts from Codex are a prior only; take them from lidar or the footprint when available (ai-textures.md, Verification 5).

`building_label.schema.json` wraps the existing facade schema in a strict building object with
`building_id` and `facades`. AOI production checks both JSON Schema and returned facade IDs.
Geometry dimensions/bay counts remain authoritative over visual label priors.

`facade_visual_qa.schema.json` v1 is the generated-facade review: storey and upper-row opening
counts in the generated image and in the references, grid/orthographic/clean verdicts and foreign
objects. `turkutex.facade_generated_qa` accepts a generated wall only when all of them agree.

`facade_reference_grid.schema.json` v1 is read from the references before generation: whether the
rectified photos show the outlined wall at all (`wall_visible`, `problem`), and its storeys, window
columns per upper row, ground-floor openings, plinth, cornice and cladding. Generate jobs skip walls
that are not visible and pass the counts to the image model as hard constraints.
