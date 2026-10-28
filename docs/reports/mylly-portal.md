# Clean tunnel mouths

The OCI Myllytunneli tiles `t0_-2_2` and `t0_-2_3` were built at
2026-10-28T21:00:36Z and 21:00:43Z with generator fingerprint
`42e0032016b704d4`. Computing the fingerprint from the pinned OCI source
`4bb7ecf460917dc945d60b9191171502a89209b0` gives that exact value.
The reported obstruction therefore was not explained by an older tunnel generator.

Tunnel mouths now have one U-shaped frame mesh each. Jambs and side-wall
thickness sit outside the clear box. High terrain fragments are removed from the
short mouth/approach footprint as well as the ordinary clearance cut; inland
high overburden remains. Existing road-floor and two-metre approach geometry
continue to serve both rendering and collision.

Barrier placement excludes each mouth, including its frame and approach, before
creating either render instances or collision boxes. This also excludes explicit
on-road fences and bollards, while preserving barriers inland on the overburden.
Barrier stage cache identity includes tunnel inputs and tunnel source dependencies.

Verification (no screenshots, browser or driving checks):

```sh
source ../sandbox.env
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_tunnels.py pipeline/tests/test_coarse_fences.py
```

All 16 affected tests pass. They cover frame clearance, high terrain removal at
mouths with retained inland terrain, road crossfall, approach continuity,
render/collision floor agreement, barrier exclusion and passage preservation.
Jonni owns the visual review of the regenerated world.
