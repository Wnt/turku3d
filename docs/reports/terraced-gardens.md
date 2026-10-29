# Source-driven terraced courtyard gardens

The Rettig reflecting pool is OSM `natural=water` way `1029612222`, 56.489 m².
The city water polygons omit it. Its complete mapped outline crosses `t0_1_0`
and `t0_2_0`; both use the same canonical ground-key blocks. The measured rim
level is 8.998 m N2000, water 8.878 m, rim top 9.058 m and bed 8.348 m.

The generator now prepares small mapped courtyard water, explicit swimming /
reflecting pools and basins, and level garden patches from lidar. Untyped small
water receives masonry only when rectangular and near a mapped garden. Complete
city-water connectivity prevents short river reaches from becoming courtyard
pools. Natural ponds retain the shallow grassy-bank algorithm. Pools receive
exact water outlines, 30 cm stone rims, inner walls and shallow collision beds;
geometry is split at visual and collision seams. Source provenance and terrace
counts appear in both pass manifests.

Mapped garden polygons and bounded path cells flatten only with sufficient
near-level ground returns; slopes are rejected. A 1.5 m halo joins the original
terrain. Generic wall-tagged edges with at least 60 cm measured crossfall receive
below-grade retaining faces instead of a coarse slope and an invented 1.8 m
above-ground wall. Surveyed shorelines and mapped stair openings retain their
own geometry. Clipped hedge prototypes now have fine leaf texture. Existing OSM
path surfaces, facade descriptions, facade-kit results and texture assets remain
in use.

Private source inspection: `~/turku3d/briefs/img/rettig-garden-40.webp` and
`-41.webp`, own orthophoto/lidar in the sandbox `reports/garden-source.png`, and
round3 capture inventory. Round3 has no scored quarter for `t0_1_0`; its
`t0_2_0_q11` capture is outside this pool's quarter. No reference imagery is
published.

Verification:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_gardens.py pipeline/tests/test_coarse_footbridge.py pipeline/tests/test_coarse_fences.py pipeline/tests/test_coarse_sources.py
python3 scripts/dev/probe_gardens.py --commit "$(git rev-parse HEAD)"
```

The selected OCI probe builds only `t0_1_0` and `t0_2_0` first/full, compares
building and collision counts with source, then verifies source pool count,
level, exact area, rim collision and seam vertices. It lists other source
candidate tiles without rebuilding them. Post-deployment measurements are
recorded in the task receipt and final report.

Deployed `59f98b7d` evidence: all31 selected tests passed; finish wall5.695s.
Pages and OCI publication succeeded. Curl smoke completed in0.316s, with healthy
HTTP integration points but red rolling generator health:6 recent full
cancellations and full p50 of29.901s. No rollback was performed.

The selective OCI audit passed. `t0_1_0`: first4.115s/full18.822s,
18 source buildings and16 collision cells. `t0_2_0`: first14.299s/full67.308s,
36 source buildings and16 collision cells. Both manifest passes match source
pool counts/levels. Pool areas are37.966914m² and18.522149m² (56.489063m² total),
with18 rim triangles per tile and identical seam vertices at
x500, y8.878, z127.92483410691177 /133.29982813644799. Terrace levels are also
identical at the tile seam. The source inventory identifies61 additional
candidate tiles with mapped gardens, pools, hedges or wall edges; they were not
rebuilt. Full list and timings are retained in
`~/turku3d/tasks/terraced-gardens/checks/59f98b7daa65699d09a606138901ccf47a5eee89/`.
