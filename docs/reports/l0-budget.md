# L0 visual wire budget

The production assembly now includes the filtered street facade export and the
4 cm/px ground binding. The first build failed at 9,049,711 brotli bytes for
`t0_0_0.glb`; its separate ground maps added 4,375,921 bytes.

The table gives decimal bytes. Before values reconstruct the original embedded
library maps from the staged GLB and compress that GLB with the server's brotli
quality 5 setting. The reconstruction differs from the directly observed first
tile by 7,521 bytes because image order and JSON layout change. After values
come from the published manifest. Ground is the three per-tile KTX2 maps
(`ortho`, `splat0`, `splat1`).

| Tile | Before GLB | Ground | Before total | After GLB | After total |
|---|---:|---:|---:|---:|---:|
| `t0_0_0` | 9,057,232 | 4,375,921 | 13,433,153 | 3,607,761 | 7,983,682 |
| `t0_0_1` | 6,425,188 | 4,267,083 | 10,692,271 | 3,361,457 | 7,628,540 |
| `t0_1_0` | 4,540,640 | 4,412,848 | 8,953,488 | 2,189,078 | 6,601,926 |
| `t0_1_1` | 5,016,435 | 4,537,593 | 9,554,028 | 3,062,658 | 7,600,251 |

The dominant `t0_0_0` GLB payload was 5,387,846 bytes of facade maps,
1,941,457 bytes of repeated library maps, and about 4.14 MB of mesh/other
uncompressed bytes. The ortho KTX2 map alone is 4,089,157 bytes. Other
per-tile ortho maps are 4.04–4.27 MB. Shared library maps now load once from
`visual/shared/`; the six referenced images total 1,941,457 bytes. Facade
normal maps are re-encoded at unchanged resolution as ETC1S. The embedded
facade maps in `t0_0_0` fell to 1,736,544 bytes. Albedo, geometry and UVs
are unchanged. Ground library PBR arrays are also shared by the existing
ground binding and are excluded from each L0 tile's per-tile budget.

Rebuild and verify in a sandbox with published layers and texture exports:

```sh
source ../sandbox.env
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-e2e
```

The last command loads the published tiles in Chrome on the sandbox's dist
port and writes screenshots to `$TURKU3D_WORK/reports/screenshots`.
