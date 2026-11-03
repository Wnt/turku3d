# Handover: floodlights2 (night light of lit sports venues)

Lit sports venues are the generator's `lights.floodlights` list. It comes from the `floodlights@1` tile migration,
`pipeline/turku3d/coarse/floodlights.py`, and holds the venues (field rectangle, kind, level) and their masts
(base, head height, aim at the venue centre). The client lights each venue's field and surroundings from this data.
Everything follows the tile data, the shared night level (sun elevation) and the shared weather, so all clients
agree (multiplayer-compatible).

- Loop state and evidence: `~/turku3d/loops/floodlights2/STATUS.md` and `evidence/` (local only).
- Earlier work: `~/turku3d/loops/floodlights/STATUS.md` (rule, migration, glare heads, mast poles).
- Code: `game/src/render/lights/floodArea.ts` (area light), `glow.ts` (dome and beams), `floodlights.ts`
  (decode, heads, poles), `index.ts` (StreetLights wiring), `render/lighting.ts` (light set).
- Tests: `game/tests/unit/flood-area.test.ts`, `flood-glow.test.ts`, `floodlights.test.ts`.

## Why the field used to be dark

The first version added each venue's field to the ground irradiance map (`groundField.ts`). Only the wet surface
family samples that map, through wetSurface's light-map hook.

- Pitch grass (`draft_pitch_grass_light|dark`) is kind `grass`: the dry surface array or MaterialStyler grass.
- Track rubber is a plain standard material, and so is the pitch paint.

So only asphalt courts were ever lit. It was not a regression of the array release, the render-group uniform move,
the lighting threshold or the shadow batches.

`flood-area.test.ts` builds dry, wet and Lambert fragment shaders through `GameLighting`. It asserts that each one
carries the venue light with an unchanged uniform-binding count, so this cannot break silently again.

## Venue light model

### Field wash and spill (`floodArea.ts`)
The venue light is one analytic area light, a data node of the session's fixed light set next to the vehicle
beams. Every lit material receives it: pitches, tracks, paint, stands, facades, trees, cars and people.

- **Geometry.** Each venue is a field rectangle (centre on the ground, long axis, half extents a x b) under lamp heads
  at height H, the mean of its resident masts' tops above the field. A venue without resident masts uses the layout
  height of its kind (stadium 30, track 24, large 20, field 16, court 10 m).
- **Direction.** The light comes from a point at height H above the field. That point is the surface point's
  projection onto the rectangle, pulled 15 % toward the centre, so the light is aimed at the field.
- **Wash.** The field is flat at `AREA.field` x level (2.6 direct, plus a 0.3 all-direction share): about 3.4 at the
  centre of a level-1 stadium and 2.9 at the touchline. A street pool under its lamp is about 0.6, open ground by day
  about 5. The middle is 12 % brighter, where beams overlap.
- **Spill.** At distance r outside the field, E = E0 x D^2/(D^2 + r^2) x (1 - (r/400)^4)^2, with D = H.
  - On the ground at Paavo Nurmi: 2.45 at 10 m, 1.06 at 30 m, 0.10 at 100 m, 0 beyond 400 m.
  - On facades facing the venue: 0.18 at 100 m, 0.04 at 200 m, 0.01 at 300 m. Night ambient is about 0.04.
- **All-direction share.** It is 0.3 x E, added to the material's indirect irradiance, so ambient occlusion still
  shapes it. It lights the vertical faces of players and stands, and fades out above 2H.
- **Slots.** The nearest 4 active venues take render-group uniforms (4 vec4 per venue, plus up and count). Their light
  is summed into one light (colour and luminance-weighted direction) for a single BRDF evaluation per fragment.
- **Hand-over.** A venue's weight falls to 0 as its distance nears the first venue left out, or 1600 m, so slot
  hand-overs never pop. By day the count is 0 and the shader skips the block.

### Dome and beams (`glow.ts`)
Both draw in the light sprites' overlay pass (`sprites.ts`). That pass is composited after TRAA and bloom, has no
depth buffer, and tests each fragment against the resolved scene depth.

- **Dome.** One Gaussian glow per resident venue (at most 16), centred 0.4 H above the field.
  - Sigmas: 0.7 a + 0.5 H, 0.7 b + 0.5 H, and 0.9 H vertically.
  - Back faces of a 2.5-sigma proxy box: the camera may stand inside it.
  - The fragment evaluates the glow's line integral in closed form (Winitzki erf) from the camera to the scene
    depth, so it is correct from inside, against the sky and against walls.
  - Radiance through the middle is 0.035 in clear air and 0.235 at full mist. `exp(-distance / fogFar)` dims a far
    venue's dome.
- **Beams.** One widening soft fan per mast, from the head 75 % of the way toward the venue centre on the ground
  (about a 20° spread).
  - The fan fades when seen end-on, where the head's glare sprite covers that view.
  - It is depth-tested like the sprites.
  - Radiance at the head end is 0.01 in clear air and 0.1 at full mist.
- **Mist.** Mist = fog + 0.35 rain + 0.1 overcast, from the shared weather sample (`NightInputs.weather`, `fogFar`).
- **Programs.** Two instanced additive draws whose programs exist from the first frame: night, mist and fog are
  uniforms, and venues and masts only rewrite instance data. By day the proxies collapse, so they shade no fragments.

The glare heads (sprites) and the poles of laid-out masts (`FloodMasts`) are unchanged from the first version. The
LED colour is now about 5700 K, `FLOOD.color`.

## Cost

These figures are estimates, not measurements: no GPU work was allowed on the Mac, and SwiftShader on
osgallery-dev streams no full tiles.

- **Lit fragments.** With one venue in range, about 45 ALU plus one BRDF per lit fragment; up to 4 venues add the
  geometry part only. Below 0.1 ms at 1080p on an M1 Max.
- **Dome.** A full-screen dome is about 70 ALU plus one depth sample.
- **Beams.** At most 192 quads.
- **Budget.** The total is well under the 0.3 ms target on medium. The same code runs on `reduced`; on the Radeon
  560X expect a few times that.
- **Uniforms and programs.** No new uniform buffer binding per lit stage (the render group already exists for the
  vehicle beams). No program per object. One lights-cache key change at startup.

## GTA V reference (techstudy, live, lowest preset)

techstudy's notes are in `~/turku3d/loops/floodlights2/STATUS.md` (captures stay local).

- **Per-lamp pools.** GTA builds field light from discrete pools, one per pole lamp.
  - Each pool is about 25–35 m across. The pools overlap along a racetrack, and the infield is darker.
  - There is no uniform floodlit floor.
  - The drawables carry mostly spot lights: intensity p50 11, range p50 35 m (p90 70), cone 10/55°.
- **Coronas.** Every lamp has a bright point corona visible from far away, sized per light (CoronaSize p90 10),
  drawn at half size and intensity in water reflections. Distant city lights are 32x32 sprite batches.
- **Haze.** No haze dome at the lowest preset. Light volumes exist in the data but render only at higher settings or
  in fog and rain.

Jonni asked for a bright pitch, so the even wash stays. The dome is subtle in clear air and strong in fog and rain,
close to GTA's volumes in bad weather.

## Open items, ranked

1. **Domes and coronas beyond about 1 km.** Venues are known only from resident draft tiles (the light list), so a
   lit stadium vanishes from the skyline past draft residency. Fix: put the venue list (centre, extents, level, head
   height) in the distant-scenery cells or a city-wide light index, and feed `FloodGlow.setVenues` and far glare
   sprites from it. The area light needs only the near venues.
2. **Roof-mounted heads.** The heads on grandstand roofs (`coarse/venues.py _roof`) are geometry only: no glare, no
   light and no part in the venue's height. Fix: emit them as masts (`mesh: 1`, top = roof head height) in the
   `floodlights` rule and migration (generator parity), and the client lights them with no change.
3. **No shadows.** The area light is unshadowed. The far tail (≤ 0.1 beyond 100 m) reaches through stands and the
   buildings in front. Cheap fixes:
   - a per-venue angular depth bin, as the vehicle beams do (`vehicleLightData.ts`), from footprint grids;
   - or fade the light behind footprints along the line to the field, sampled on the CPU per slot.

Smaller follow-ups:
- a mild per-mast pool pattern on the wash for GTA-like structure;
- coronas in the river reflection at half size;
- a measured bench (`bench=static` at night at a venue) on the 560X.
