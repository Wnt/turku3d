# Visual brief: recognisable Turku through a new world pipeline (W2)

2026-11-12. Replaces the riverside-corridor brief of the same day. The prepared scenes (`office`, `port-arthur`)
improved single corridors by hand; the blind judge still scored the eight pilot locations 39.3 / 100 overall and
3.7 / 10 for identity. The campaign writes a new world pipeline so that game stills become recognisable as the real
Turku, at the pilot locations and on streets nobody tuned by hand. Its contracts are in
[world-pipeline.md](world-pipeline.md).

## Goal and measure

1. **Pilot views.** Raise the blind visual judge's scores on the 24 pilot views
   (`scripts/dev/views/pilot-locations.json`): overall (fidelity) from 39.3 / 100, identity (recognisability) from
   3.7 / 10.
2. **Random streets.** Raise the mean overall score of a hidden set of 24 random on-street locations in built-up
   Turku by at least 25 % relative to that set's own frozen-city baseline.

Baseline by location (overall / identity):

| Location | Baseline | What it proves |
| --- | --- | --- |
| Portsa (Port Arthur) | 44.8 / 4.2 | wooden houses, cobbles, courtyards |
| Humalistonkatu | 44.0 / 4.3 | apartment-block street with street trees and shopfronts |
| Jokiranta (Läntinen Rantakatu) | 41.2 / 4.0 | quay, river and water, bridges, stone frontage |
| Kauppatori | 40.8 / 4.0 | open square, mixed historic and modern frontage, market furniture |
| Nummi | 38.8 / 3.8 | detached-house area: gardens, hedges, low-rise roofs |
| Puolalanpuisto | 38.1 / 3.3 | park terrain, stairs and slopes, mature trees |
| Kupittaa | 34.4 / 2.8 | new office and campus blocks, railway, park edges |
| Rettigin palatsi | 32.4 / 3.2 | landmark facade and its riverside setting |

Judge weights: identity 0.30, buildings 0.20, materials 0.15, street 0.15, lighting 0.10, defects 0.10. Identity,
buildings and materials are 65 % of the score and all start near 3 / 10: **facades decide**. The judge's noise is
about 5 points; a smaller change is not a gain.

**Recognisability is the bar.** Someone who knows Turku must recognise each building in a view as the real
building: its massing and roofline, storeys, bay rhythm and openings, colour and material, ground floor, ornament
and landmark features. A plausible generic building in the right place is a failure, however rich it looks.

## Decisions (project owner, 2026-11-12)

- **Clean rewrite of the world pipeline, pilot locations first.** New data model, generators, formats and loader.
  Source-data readers and measured data (facade programs, surveys, the texture library) carry over. The old tile
  generator, its caches and the prepared-scene compiler are not built upon.
- **Same game client.** The three.js renderer stays; its lighting and materials are improved where the judge shows
  gaps.
- **Two contrasting locations first:** Humalistonkatu (stone-built apartment street) and Portsa (wooden houses).
  Then the other six, then random streets.
- **Measure, then generate.** Vision models read panoramas into per-building programs. Geometry and textures are
  procedural. No photo pixels in the game and no AI-painted textures.
- **Tiered authoring, all stored as data.** Every building gets an automatic fit. Salient buildings (ranked
  automatically, plus those in pilot views) get a reviewed program from several panoramas. Landmark parts come from
  parametric components. No building ids or coordinates in code.
- **Stills only for now.** No gameplay, collision, streaming or deployment work is required for a milestone, and
  none of them is designed out. Frame time, triangles and texture memory are recorded per location; they do not
  hold work back.

## Design constraints

1. **The unit of work is one entity.** A building, a street surface, a tree group: each has a stable identifier, a
   measured description stored as data, and a compiled, content-addressed output. Changing one building rebuilds
   one building in about a second and shows it in the running client without a reload.
2. **A location is a query.** One command builds any street in Turku (`--at E,N --radius R`) with no hand steps and
   yields a manifest the client loads. City scale later means a spatial index and levels of detail over the same
   store.
3. **Descriptions become real geometry.** Openings are recessed, with frames, sills and surrounds. Plinths,
   cornices, balconies, dormers, chimneys and porches are geometry from Turku-specific kits. Materials are
   procedural PBR families.
4. **Observation is automatic.** Panoramas → rectified wall crops → a structured reading by a vision model → render
   and compare against the photo → stored with provenance and confidence. A wall nobody photographed falls back to
   register, LoD2, lidar and orthophoto priors.
5. **The same commands at every location.** Anything that works at only one location is a defect, unless it is a
   named landmark component.

## Phases

| Phase | Content | Exit |
| --- | --- | --- |
| P0 | Contracts, walking skeleton, hidden and dev random sets with baselines | Skeleton stills of both first locations through the game client |
| P1 | Humalistonkatu and Portsa (6 views) | Both at least 10 points above their baselines, built by the same commands |
| P2 | The other six pilot locations, landmark components | Full 24-view judge run |
| P3 | Random streets: on-demand build at any point; dev set, then the hidden set | Hidden-set mean ≥ 1.25 × baseline |
| P4 | Depth | Work goes where weight × deficit is largest |

P3 starts as soon as P1 exits, beside P2: generality is cheaper to keep than to retrofit.

## Tracks

| Track | Owns |
| --- | --- |
| `w2-core` | Data model, store, location driver, client loader, capture integration, performance tracking, background completeness |
| `w2-buildings` | Building description schema ([building-description.md](../specs/building-description.md)), workbench, program → geometry and materials, roofs, part kits |
| `w2-observe` | Panorama selection, rectification, vision reading, render-and-compare, tiers, stored descriptions |
| `w2-street` | Terrain and ground surfaces, kerbs, markings, furniture, trees, hedges and fences, water |
| `w2-look` | Lighting, shading and material response in the renderer |
| `w2-eval` | Random sets, baselines, milestone judging. Never develops generators |

## Measurement and acceptance

- **Judge:** fixed rubric (`scripts/dev/judge/JUDGE.md`), two repeats, only at visual milestones, in the
  background. Stills come from `scripts/dev/scene-capture.mjs --mode render` at the cameras of the reference photos
  (`qa/views/views.jsonl`, tag `pilot`).
- **Look before you claim.** Read your own render beside the photo. A milestone is obviously better at normal view
  distance; a benefit that needs zooming is bundled into larger work. Stop or redirect an experiment when an early
  comparison shows no visible gain.
- **Hidden set:** 24 random street points, one building-targeted view each, made like the pilot views. Only
  `w2-eval` and the coordinator read it; workers get the aggregate and generic themes, never locations.
- **Dev set:** 12 other random points, open to all workers.
- **Imagery:** the pipeline may read any panorama, including those the judge's photos come from, because that is
  how the product works. The hidden set tests that the automatic pipeline works on streets nobody tuned by hand.
- **Tracked without a limit:** frame time on the M1 Max at medium quality, triangles and texture memory per
  location, recorded in the capture manifest.
- `city.madekivi.fi` and the earlier Pages release stay frozen.

## Background

The riverside-corridor brief this replaces and the campaign before it are in Git history and
[docs/archive/recognisable-turku-campaign-2026-11.md](../archive/recognisable-turku-campaign-2026-11.md). How
studios and research reach recognisable buildings, and which models and code we may use, are in
[procedural-buildings-streets.md](../research/procedural-buildings-streets.md) (§5, §7, §7.1).
