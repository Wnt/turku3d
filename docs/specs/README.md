# docs/specs/

Binding formats shared between the pipeline (Python), the game (TypeScript) and the validator (Node).

- [col-format.md](col-format.md): `.col` collision tile binary v1.0
- [materials.md](materials.md): uint8 surface material table v1 (append-only)
- [facades.md](facades.md): buildings → facade texturing metadata, ordering and metre UVs
- [tiles.md](tiles.md): tile ids and grids, `dist/` layout, `tileset.json`, `manifest.json`

Change a spec first, then its implementations. Numbers (origin, sizes) come from `pipeline/turku3d/config.py`; tests check that config and specs agree where they overlap.
