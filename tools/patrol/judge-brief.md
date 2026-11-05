# Patrol judge brief

You judge the candidates of one patrol run of Turku3D, a GTA-style open world of Turku (central Turku at
street level, daytime, medium quality, 960x540). Judge what a player standing or driving there would notice.

Inputs in RUN_DIR (paths in candidates.json are relative to it):
- `candidates.json`: one entry per detector hit: `n`, `spot`, `view`, `detector`, `class`, `metrics`, `image`.
- `crops/*.png`: the annotated image of each candidate (magenta box; temporal candidates show the first frame next
  to a heat map; pits show the raised view next to the ground grid, dark = low; pops show frames t-1, t, t+1).
- `spots/<spot>.png`: contact sheet per spot (h0, h1, h2, raised, then shimmer and wander heat maps).

For every candidate, write one verdict (copy its `image` path so the verdict survives re-numbering):

```json
{"n": 12, "image": "crops/x.png", "real": true, "severity": 4, "confidence": 0.8, "title": "short defect name",
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
