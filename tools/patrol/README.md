# Visual patrol

A re-runnable street-level patrol of central Turku on the deployed game (city.lab QA mode, no multiplayer join).
It captures fixed spots and short drives, runs automatic detectors, has a vision judge describe each hit, and ranks
the real issues by player impact.

## Run

```sh
S=~/turku3d/tools/host-lease/session.py
OUT=~/turku3d/loops/patrol/$(date +%Y%m%d-%H%M)
# 1. colour pass: stills, still-camera sequences, collision grids, drives (Windows, ~12 min)
$S run win patrol --kind func --load-timeout 180 --headless --window-size 960,540 -- \
  node tools/patrol/capture.mjs --out $OUT --pass color
# 2. id pass at the same poses (~3 min; reads $OUT/poses.json)
$S run win patrol --kind func --load-timeout 180 --headless --window-size 960,540 -- \
  node tools/patrol/capture.mjs --out $OUT --pass id
# 3. detectors -> $OUT/candidates.json, crops/, spots/ contact sheets
~/turku3d/envs/pipeline/bin/python tools/patrol/analyze.py $OUT
# 4. vision judge: a Claude subagent reads judge-brief.md with candidates.json and writes $OUT/judge/*.json
# 5. ranking -> $OUT/ranked.json, report.md
python3 tools/patrol/rank.py $OUT --top 10
# 6. archive (the Mac is short of disk)
rsync -a $OUT osgallery-dev:/bulk/turku3d/patrol/ && rm -rf $OUT
```

`--only id1,id2` limits a pass to some spots or drives; `--base URL` points at another build (a local dist);
`--size 960x540`, `--frames 60` (still sequence length) and `--speed 12` (drive m/s at a virtual 30 Hz step).

## What it captures (spots.json)

- 15 spots: Kauppatori (2), Aurakatu, Linnankatu at the pizzeria, both riverside banks, Auransilta and its
  north-end underpass, Tuomiokirkkosilta and its stairs pose, the cathedral square, Martinsilta, the Myllyahde
  tunnel, Kupittaansilta and the Vartiovuori park. Coordinates are EPSG:3877 from OSM; engine x = E - 23459500,
  z = -(N - 6704500).
- Per spot: the collision ground height (camera 1.7 m above it), stills at 3 headings and a raised view (+25 m,
  pitch -22), the same street stills with the population hidden (a car/walker mask), an 80 x 80 m collision height
  grid, and a 60-frame still-camera sequence with cars and walkers hidden.
- 3 drives (400 m each: Aurakatu, Läntinen and Itäinen Rantakatu) with one fixed 0.4 m step per rendered frame, so
  pops are measured per frame independent of the frame rate; stutter comes from the real frame intervals.
- The id pass (`?pass=id`) renders the same poses as object ids with `__qaIdTable` (kind, layer, material, tile).

## Detectors (analyze.py)

| detector | signal | threshold |
|---|---|---|
| shimmer | 24 px blocks of the temporal high-pass mean \|L_t - (L_t-1 + L_t+1)/2\| (foliagestable's d2) | > 2.5 luma over >= 3 blocks; class from the id pass |
| wander | \|L_last - L_0\| over the sequence where the high-pass is low (slow drift) | > 9 luma over >= 3 blocks |
| hole | id 0 (nothing drawn) below the horizon, closer than ~300 m | >= 0.04 % of the image; sky-coloured in the colour pass = high severity |
| pit | collision grid cells > 1 m below the median within 30 m | >= 24 m2 |
| blank-facade | window-like (darker or bluish) pixels on a facade_kit/component building filling >= 5 % of a street still | < 3.5 % |
| lod-shell | block-LOD shells (layer building_lod) in a street still | > 3 % of the image |
| white-car | population blobs (with vs without population) that are flat near-white | white > 55 %, luma std < 14 |
| pop | drive block differences 4x their +-8-frame median and > 14 luma | >= 4 blocks; population hidden |
| stutter | drive frame intervals | > max(3 x median, 100 ms) |
| collision-gap | collision grid points with no surface at all inside flat walkable ground (>= 5 of 9 neighbours have one, within 0.5 m of each other; steep faces are not in the ground index) | any |
| untextured | median high-pass of one object's pixels (>= 3.5 % of a street still) | < 0.15 (placeholders 0.00-0.01, textured >= 0.35) |
| not-settled (delivery) | coarse.viewStatus not ready or not quiet within 180 s, also on the end-of-run retry | any |

## Judge and ranking

The judge (judge-brief.md) gets every candidate's annotated crop and metrics plus the spot contact sheets, marks
each candidate real or false, gives severity (1-5), confidence (0-1), a title, the suspected cause and owner, and
an `issue` key that merges the same defect seen at several poses; it may add judge-only findings the detectors
missed. rank.py scores `severity * confidence * visibility * street * centre * sqrt(frequency)`.
