# turku3d.ground

`stub.py` — Phase 0 stub ground only: `heightfield()` (linear TIN via scipy + nearest fill, or a numpy binned
mean + diffusion fill without scipy), `grid_mesh()` (upward-facing triangles on an O-aligned grid, so tiles share
bitwise-identical seam vertices) and `load_ground_points()` (maanpinta class 8 key points from
`raw/laz/maanpinta/*.laz`; missing neighbour LAZ tiles in the margin are skipped). Used by
`turku3d.package.stub_tile`. The Phase 1 ground (class 2 + 12 merge, curb breaklines, CDT) goes in this package too.

```bash
make tile-test   # tests/test_package.py covers the heightfield, grid orientation and seams
```
