# Patrol judge brief

You judge the candidates of one patrol run of Turku3D, a GTA-style open world of Turku (central Turku at
street level, daytime, medium quality, 960x540). Judge what a player standing or driving there would notice.

Inputs in RUN_DIR (paths in candidates.json are relative to it):
- `candidates.json`: one entry per detector hit: `n`, `spot`, `view`, `detector`, `class`, `metrics`, `image`.
- `crops/*.png`: the annotated image of each candidate (magenta box; temporal candidates show the first frame next
  to a heat map; pits show the raised view next to the ground grid, dark = low; pops show frames t-1, t, t+1).
- `spots/<spot>.png`: contact sheet per spot (h0, h1, h2, raised, then shimmer and wander heat maps).

For every candidate, write one verdict (copy its `n`, `image`, `spot`, `view` and `detector` so the verdict survives re-numbering):

```json
{"n": 12, "image": "crops/x.png", "spot": "kauppatori", "view": "h0", "detector": "pit", "real": true, "severity": 4, "confidence": 0.8, "title": "short defect name",
 "issue": "stable-key-for-this-defect", "description": "what is wrong, one or two sentences",
 "cause": "suspected cause", "owner": "likely owner area"}
```

- `real`: false for a detector false positive (an intentional feature, expected motion such as clouds, or a
  detector artefact). Say why in `description`.
- `severity` (player impact if real): 5 breaks play or immersion everywhere it is seen (falling through, huge
  holes, the street itself wrong), 4 an obvious defect at street level, 3 noticeable, 2 minor, 1 cosmetic.
- `confidence`: how sure you are that it is real and correctly described.
- `issue`: the same key for the same defect seen at several spots or views (e.g. `kauppatori-pit`,
  `tree-shimmer`), so the ranking can count frequency.
- Owner areas: ground generator (terrain, lidar pits), ground QA (holes/seams), bridges, facades, vegetation,
  TAA/rendering, streaming/LOD, cars, traffic, props/street furniture, water, sky.

Then look through every contact sheet for defects the detectors did not flag and add them as judge-only entries
with `"n": null` and their own `spot`, `view`, `image` (the still's file name), `E`, `N` from candidates of that
spot, plus the fields above. Only add what a player would clearly notice.

Write a JSON list of verdicts to the file you are told to write. Be strict: a candidate is real only if the image
shows the defect.

## Real-city comparison (pano-diff candidates)

`crops/pano-<spot>-<view>.png` shows the game still (left, GAME) next to a Google Street View crop at the same
position, heading, pitch and field of view (right, REAL, capture month in the label). The panorama is the real-city
baseline. List what a player would see as wrong compared with reality, most important first:
missing objects (buildings, trees, street furniture, signs, kerbs, parked cars, fences), wrong materials or colours
(facade cladding, roof, paving, asphalt vs gravel), wrong geometry (building height, massing, roof shape, street
width, slope). Ignore: weather, time of day, lighting, people and moving cars, small pose offsets (a few metres or
degrees), season, and things the game deliberately leaves out (shop signs and text, brands). A pano-diff verdict is
`real: true` when at least one clear difference exists; put the differences in `description` (semicolon-separated)
and use one `issue` key per difference class that recurs across spots (e.g. `missing-street-trees`,
`facade-material-wrong`, `missing-kerb-parking`), so the ranking can count how often the city-wide rule fails. When
several distinct classes appear in one pair, add one verdict per class (same n and image, different issue).

## Untextured candidates

A large object drawn as one flat colour without texture detail. Real when the surface should clearly carry a
texture (asphalt, paving, concrete, masonry, facade); false when it is legitimately smooth at that distance (sky
reflections on glass, painted plaster in shade, water).

Only settled frames reach you: every pose was captured after its 3x3 tiles were on their full pass and all scenery
cells in view had loaded. Delivery problems (poses that never settled, failed loads) are reported separately and
are not yours to judge.
