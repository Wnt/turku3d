# Unioninkatu tunnel entrance

The generic box-tunnel generator now uses open mapped approaches to distinguish
road elevation from the hill covering a portal. A stable low-gradient fit three
to nine metres outside an entrance can replace a contaminated end sample. It
also prevents the coarse terrain grid from lifting a correct measured floor.
Uncertain fits preserve the previous measured profile.

At the vehicle entrance in `t0_-6_7`, the original portal sample was N2000
12.244 m, while the road two metres outside was 4.88 m. The reconstructed floor
at that entrance is 4.857 m. The shared shell
meets the opposite approach at 3.277 m, instead of being raised by the coarse
terrain to about 11.36 m. The road and cycleway share one 4.5 m clear opening,
with a 12 cm curb separating their mapped lanes.

`coarse/portals.py` owns the mapped approach association, robust floor anchors,
bank-following concrete headers, narrower jambs, retaining wings and complete
cross-section mouth cuts. Corrected approaches meet the intact outer road
cross-section without reproducing a hump from the hill's interpolated grid.
Nearby full-length parallel road/path ways on the same tunnel level share a
curved shell when their lateral gap is at most two metres; distinct levels and
transverse crossings stay separate. Terrain remainders retain the normal ground imagery and material palette instead
of appearing as grey tunnel-concrete triangles. Geometry helper modules are
registered in served visual and collision identities as well as stage caches.

The supplied game screenshot and local lidar/key-point profiles were inspected.
The original round-3 inventory lacked this quarter, so a new bounded native
Google 3D capture was made in the sandbox: 494/494 terminal nodes, no missing
payloads. A fresh 2026 street-level reference confirms the common rectangular
opening and low lane separator. Reference imagery, captures and diagnostic
component images stay local; none are published in Git.

Other box entrances use the same fitting and bank treatment, including the two
Unioninkatu cycleway/vehicle ends in `t0_-6_6`, Myllyahde and unnamed mapped
service-tunnel entrances. A floor moves only when the open approach supplies
consistent evidence. Building passages, barrel vaults and carried-road structures
keep their separate construction rules. The local component pass for both
Unioninkatu ways took about three seconds and produced 1,902 triangles in the
visible tile; this is not a whole-tile build timing.

## Verification

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_portals.py pipeline/tests/test_coarse_tunnels.py pipeline/tests/test_coarse_footbridge.py
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_sources.py -k collision_identity
bash pipeline/env/run.sh python scripts/dev/probe_tunnel_portals.py --commit "$(git rev-parse HEAD)"
```

The last command builds affected first/full passes for both tunnel-end tiles on
frozen deployed OCI source, compares counts with source, decodes the packaged
collision and casts floor/ceiling-clearance probes across the shared road and
cycleway opening. The agent-owned run has a hard ten-minute budget including
slot waits. The earlier OCI test hold was lifted before this final selection.
An optional `--local --root PATH --wfs-cache PATH` mode retains the same packed
collision checks for sandbox builds. Missing local pole-vector layers were
fetched into a sandbox-only WFS overlay, leaving shared raw inputs read-only.
