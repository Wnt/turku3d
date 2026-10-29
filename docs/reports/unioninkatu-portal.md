# Unioninkatu tunnel entrance

The generic box-tunnel generator now uses open mapped approaches to distinguish
road elevation from the hill covering a portal. A stable low-gradient fit three
to nine metres outside an entrance can replace a contaminated end sample. It
also prevents the coarse terrain grid from lifting a correct measured floor.
Uncertain fits preserve the previous measured profile.

At the vehicle entrance in `t0_-6_7`, the original portal sample was N2000
12.244 m, while the road two metres outside was 4.88 m. The reconstructed floor
at that entrance is 4.857 m. The other vehicle end remains at its measured
3.246 m, instead of being raised by the coarse terrain to about 11.36 m. The
vehicle opening keeps 4.5 m clearance. The adjacent cycleway retains a 2.5 m
default opening; its opposite contaminated end is corrected to 3.364 m.

`coarse/portals.py` owns the mapped approach association, robust floor anchors,
bank-following concrete headers, narrower jambs, retaining wings and complete
cross-section mouth cuts. Corrected approaches meet the intact outer road
cross-section without reproducing a hump from the hill's interpolated grid.
Terrain remainders retain the normal ground imagery and material palette instead
of appearing as grey tunnel-concrete triangles. Geometry helper modules are
registered in served visual and collision identities as well as stage caches.

The supplied game screenshot and local lidar/key-point profiles were inspected.
The retained round-3 capture inventory has no `t0_-6_7` capture, so it provides no
independent Google mesh measurement of this entrance. Diagnostic component images
and source inputs stay in the sandbox; none are published in Git.

Other box entrances use the same fitting and bank treatment, including the two
Unioninkatu cycleway/vehicle ends in `t0_-6_6`, Myllyahde and unnamed mapped
service-tunnel entrances. A floor moves only when the open approach supplies
consistent evidence. Building passages, barrel vaults and carried-road structures
keep their separate construction rules. The local component pass for both
Unioninkatu ways took about three seconds and produced 2,266 triangles in the
visible tile; this is not a whole-tile build timing.

## Verification

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_portals.py pipeline/tests/test_coarse_tunnels.py pipeline/tests/test_coarse_footbridge.py
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_sources.py -k collision_identity
bash pipeline/env/run.sh python scripts/dev/probe_local_tunnels.py --root "$TURKU3D_WORK/reports/tunnel-builds" --wfs-cache "$TURKU3D_WORK/cache/tunnel-wfs" --commit "$(git rev-parse HEAD)"
```

The last command is the agent's selective post-deploy run during Jonni's OCI test
hold. It builds real first/full passes locally for both portal tiles, checks
building/collision counts against source, decodes the packaged collision and
casts vertical floor/ceiling-clearance probes across the road and cycleway. A
small read-only OCI source-hash receipt confirms the published implementation;
it starts no remote build. The missing local pole-vector layers were fetched
into a sandbox-only WFS overlay, leaving shared raw inputs read-only.
