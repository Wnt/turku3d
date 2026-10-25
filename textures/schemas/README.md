# schemas/
JSON Schemas for `codex exec --output-schema` (strict: every property required, `additionalProperties: false`,
enums). `facade_label.schema.json` v1: facade spec for the procedural facade generator and for picking usable views.
Bay counts from Codex are a prior only; take them from lidar or the footprint when available (ai-textures.md, Verification 5).

`building_label.schema.json` wraps the existing facade schema in a strict building object with
`building_id` and `facades`. AOI production checks both JSON Schema and returned facade IDs.
Geometry dimensions/bay counts remain authoritative over visual label priors.
