# Visual brief: contrasting pilot locations

2026-11-12. Replaces the ten-hour campaign plan (archived in
[docs/archive/recognisable-turku-campaign-2026-11.md](../archive/recognisable-turku-campaign-2026-11.md)) after the
campaign review: 14 accepted increments had too little combined visual impact, small defensible details and
deployment counts stood in for whole-scene improvement, and dominant problems stayed visible. The objective is more
visible improvement per hour: complete street scenes that someone who knows Turku finds substantially more
convincing. A 10× gain in impact per hour is a target, not a measured or promised speedup.

## The brief

> Make one 100–150 m riverside corridor visibly superior to the frozen city in three fixed, normal gameplay views and
> a short walking clip. Complete everything visible from that corridor. Use reusable families, with selected landmark
> exceptions. Maintain the existing average 30 FPS target on the M1 Max at medium quality.

The first corridor is Läntinen Rantakatu along the office block and quay (prepared scene `office`). Its views and walk
are [scripts/dev/views/riverside-office.json](../../scripts/dev/views/riverside-office.json): `office-frontage`, `quay`
and `along-frontage` at the normal 4.5 m orbit distance, 15 June 14:00, clear weather, medium quality, 1440×960 at
render scale 0.5, with a forward walk from the player's position. Change a set only deliberately, and recapture the
baselines when you do.

## Pilot locations

Effort concentrates on a few contrasting places in Turku before anything is generated city-wide (project owner,
2026-11-12). Each one gets the same treatment as the first corridor: a compact playable area, a view set of three
fixed gameplay views plus a walk in `scripts/dev/views/`, and baselines from the frozen city and the lab.

| Location | What it proves |
| --- | --- |
| Jokiranta (Läntinen Rantakatu, the first corridor) | quay, river and water, bridges, stone frontage |
| Kauppatori | open square, mixed historic and modern frontage, market furniture |
| Puolalanpuisto | park terrain, stairs and slopes, mature trees |
| Rettigin palatsi | landmark facade and its riverside setting |
| Humalistonkatu | apartment-block street with street trees and shopfronts |
| Kupittaa | new office and campus blocks, railway, park edges |
| Portsa (Port Arthur, prepared scene `port-arthur`) | wooden houses, cobbles, courtyards |
| Nummi | detached-house area: gardens, hedges, low-rise roofs |

Families (materials, facade programs, vegetation, furniture) are judged across these places: a family that only works
in one of them is a landmark exception, not a family. City-scale generation starts once the families win at the
pilot locations.

A small playable boundary is acceptable. Obvious empty surroundings, missing street walls or a white void at the end
of the street are not acceptable in a completed milestone. Reuse the existing context as input while improving the
new scene.

## Priorities: what covers most of the image

The final campaign images still showed broad uniform grey surfaces, weak facade shading, uniformly dark windows,
unconvincing tree crowns, repetitive water and incomplete surroundings. These come before decorative details.

1. **Lighting and materials:** coherent daylight, facade contact and shadow depth, believable window response, ground
   variation and a consistent material scale.
2. **Buildings and frontage:** convincing nearby entrances and windows, plinths, sills, cornices, shopfront details
   and recognisable landmark features.
3. **Vegetation and surroundings:** mature tree volumes, a complete opposite bank and background, appropriate street
   furniture and a convincing river.

Use measured placement where it exists and documented visual priors elsewhere. Do not require a new vision
characterisation or a generated texture for every building: use reusable material and facade families, with
selected landmark exceptions.

## Organisation

- **Worker 1:** lighting and materials across the corridor.
- **Worker 2:** buildings and near-field frontage in the same corridor.
- **Worker 3:** vegetation and surroundings in the same corridor.
- **Coordinator:** visual direction, integration and whole-scene comparison.

Every worker uses the shared appearance loop and capture command ([DEVELOPING.md](../DEVELOPING.md#appearance-loop));
routine evidence production is not anyone's whole role. A worker brief states the visible outcome, reference
locations, scope, time budget and stop criterion, and nothing about unrelated historical process. Commit often;
publish related visible changes together about every 30 minutes; the coordinator does not repeat a worker's
acceptance work.

## Acceptance and stop rules

- **Recognisability is the bar.** Someone who knows Turku's buildings must easily recognise each building in the
  views as the real building: its massing and roofline, storeys, bay rhythm and openings, colour and material,
  ground floor, ornament and landmark features. A plausible generic building in the right place is a failure, however
  rich it looks; generic kits are only the vocabulary that a building's fitted program draws from. How studios and
  research reach this, and which models and code we may use, are in
  [procedural-buildings-streets.md](../research/procedural-buildings-streets.md) (§7, §7.1).

- Compare against both the frozen city release `63b0b015` (city.madekivi.fi) and the previous lab version
  (city.lab.madekivi.fi), with the three views and the walking clip at matched camera, FOV, time, weather, quality and
  resolution (`scene-capture.mjs --baseline`; the manifest says whether a comparison is controlled). Lost context or
  recognisability is a regression.
- A milestone is obviously better at normal gameplay distance. If its benefit needs zooming in or an explanation of
  where to look, bundle it into larger work instead of counting it.
- Check collision and movement when geometry changes (the walking clip, plus cheap affected tests). Check
  performance for the changed feature with a representative measurement under an exclusive lease.
- Stop or redirect an experiment after an early comparison if the expected visible gain is absent.
- Do not scale beyond the pilot locations until they win overall against the frozen baseline; a new pilot location
  joins the set only deliberately.
- `city.madekivi.fi` and the earlier Pages release stay frozen. Experiments are published only to city.lab.

## What is measured

Not iteration or merge counts. Track:

- **Major visible defects eliminated** in the corridor views (for example: uniform grey ground, flat facades, dark
  uniform windows, tree crowns, water, white void), each named with its before/after capture.
- **Complete scenes improved:** views and clip that win against both baselines.
- **Judge score per iteration:** the blind visual judge's overall score (0–100) and its six categories for the
  corridor's gameplay and reference-photo views (`scripts/dev/scene-judge.mjs`), compared with the previous iteration
  and with the spread between its repeats. A gain smaller than that spread is not a gain. The baseline on 2026-11-12
  (city.lab `d3e2021d`) was 45.7 ± 2.5. A visually identical local build scored 45.8, so the judge reproduces within
  its spread. History: `~/turku3d/loops/judge/riverside-office.jsonl`.
- **Iteration latency:** edit to visible (live patch), publish, capture and deploy, from the timings the commands
  print. Report an unmet visual goal plainly; deployment alone is not success.

## First trial

1. Keep the policy and capture-path cleanup to 20–30 minutes; defer tooling that does not block the trial.
2. Run a 60-minute visual proof on the first corridor (jokiranta) and its three views.
3. At minute 30, look at the whole scene and redirect if the improvement is not already obvious.
4. At minute 60, integrate the strongest coherent result, capture the comparisons and walking clip, and publish to
   city.lab.
5. Judge visible impact and measured iteration latency before extending the campaign.

## Background

The archived campaign plan keeps the reference study, the reconstruction notes and the rendering lessons. Campaign
evidence of the previous run is under `~/turku3d/wt/realism-20261112/work` and
[city.lab.madekivi.fi/progress](https://city.lab.madekivi.fi/progress/).
