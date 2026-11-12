# Coordinates and grids

The world frame shared by the pipeline, the game, tools and evidence. The numbers come from
`pipeline/turku3d/config.py`; do not redefine them elsewhere.

- **Reference system:** EPSG:3877 (ETRS-GK23) with N2000 heights.
- **Origin:** O = E 23459500, N 6704500, H 0 (the NW corner of LAZ tile 23459500_6704500).
- **Engine axes** (metres, Y up): x = E − 23459500, y = H, z = −(N − 6704500).
- **Visual tile:** L0 = 250 m, ids `t0_i_j`. **Collision tile:** 62.5 m (16 per visual tile).
- **First playable block:** Läntinen Rantakatu 15, E 23459668 N 6704254.

Formats built on this frame: `.col` collision ([col-format.md](col-format.md)), surface materials
([materials.md](materials.md)), pipeline layers and their seam rule ([layers.md](layers.md)), prepared regions
([prepared-regions.md](prepared-regions.md)). Camera poses in evidence (`scripts/dev/views/*.json`) use engine
metres and a world quaternion.
