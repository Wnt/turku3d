# Areas

The generated office-block and work-3x3 manifests reflect `turku3d.config`.
For new regions, add `<name>.yaml` with `tiles` (L0 IDs) or `polygon_en` (EPSG:3877 vertices).
See [region runner](../../pipeline/turku3d/region/README.md) for the schema and exact commands.
`next-ring` selects the twelve tiles surrounding the current four-tile office AOI.
