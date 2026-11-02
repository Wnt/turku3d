# Runtime flicker audit

Use the existing capture helper and an acquired chrome-guard slot with your own
long-lived Chrome/CDP page and port. Example (QA owns 5930/5937):

```sh
python3 ~/turku3d/tools/chrome-guard/guard.py acquire qa
node tools/dev/capture-loop.mjs --base http://localhost:5930/ \
  --cdp http://127.0.0.1:5937 --guard-owner qa --only pano-library \
  --at 13:00 --flicker 8 --out /tmp/flicker
PYTHONPATH=pipeline python3 -m turku3d.flicker_qa /tmp/flicker/POSE-flicker
```

`--cameras FILE --only ID` accepts custom poses using the shared camera schema.
The helper applies its usual full-tile, scenery, vegetation and asset readiness
gates. Each sequence has 8 warm-up renders followed by N lossless frames at each
of three fixed camera positions: still, 5 mm left, 5 mm right. `--micro-step` sets
the distance (0–5 cm). Each PNG records its exact camera, hour, products/hashes,
mesh UUIDs, geometry UUIDs, attribute revisions and material/depth-bias revisions.
Rendering pauses between frames and restores the original position afterwards.
It uses the QA mode's frozen procedural animation time; avoid motion-mode captures.

The analyzer measures repeated-pose temporal range. It also compares micro-step
mean colours to a one-pixel colour envelope around the still, excluding changes
explainable by small image motion. It writes `flicker.json`, a heatmap, and an
overlay. Default threshold is 8/255. It lists product/mesh changes by frame and
labels interior instability, edge shimmer and swaps separately. These are
heuristic suspects: true z-fighting needs coplanar geometry/depth evidence, TAA
needs an AA-off comparison; animated shaders and parallax can cause residuals.
Eight warm-up frames reduce convergence noise but do not guarantee convergence.
An audit with no detected instability is limited to its sampled poses and steps.

At runtime, highlight the geometry validator's coplanar candidates:

```js
await window.__dev.coplanarDebug(report); // JSON from geometry_qa
await window.__dev.coplanarDebug(null);   // restore original materials
await window.__dev.geometryState();      // identities/revisions for diagnostics
```

The toggle uses source object IDs retained in shipped material batches. Entire
batches become magenta; the report retains the individual source ranges. This is
diagnostic rendering only, has no work while disabled and adds no persistent
passes or meshes. Normal rendered output is unchanged.
