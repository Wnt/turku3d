# Finnish vehicle fleet (real models only)

Every vehicle role draws a real model from `models/*.json` (`registry.ts`): converted CC BY sources (Sketchfab, the
CARLA Simulator set; credits in `public/assets/LICENSES.md`) and Blender-authored models. There is no procedural or
Kenney fallback any more.

- Traffic and parking: the `sedan`, `hatchback-sports`, `suv` and `van` families (`SELECTABLE_FAMILIES`), weighted
  by each model's `weight`. Models keep their true size; a parking record only receives a model that fits its free
  space (`fitsSlot`), and a slot no model fits stays empty.
- Starter car and remote players' starter cars: `PLAYER_MODEL` (Golf VII GTI), drawn by `actors/car.ts`, driving
  with its own tuning from the registry spec (`vehicle/modelTuning.ts`). Parked cars spawn with their model's tuning.
- Fallback: `FALLBACK_MODELS` are resident in the game; a car whose model fails to load is drawn as one of them.
- Special roles (bus, trucks, ambulances, fire truck, microcar) are registered but need an exact model id.
- The trophy truck (KORPI 440, `actors/prerunner/`) is its own original model and stays separate.

`visuals.ts` keeps one instanced draw per visible model and LOD (shared node material; glazing is a second instanced
pass), four authored wheel hubs per near car (LOD0/1; LOD2 keeps them merged), paint and lamp state as instance
attributes. LOD boundaries are 42 and 105 metres; vehicles beyond 320 metres are culled. Only the nearest LOD casts
shadows. `vehicle-gallery.html` (also on the public build) shows and drives every model.

To inspect real parking in an existing fixed-camera game page:

```js
await import('/src/actors/fleet/qa.ts').then(m => m.installFleetQA());
await window.__game.fleetQA.refresh();
window.__game.fleetQA.snapshot();
```

Authored Blender models use `tools/dev/fleet-mcp.py --build MODEL.json` through
Blenderwright MCP. Small fitted parts such as window panes can be listed in
`preserve_lod_objects`. Their evaluated meshes stay separate during decimation;
their triangles are reserved before reducing the body and four wheels to the LOD
budget. Packing still merges matching material roles into shared instanced draws.
Inspect all three LOD renders: a valid triangle count does not guarantee intact glass.
Native street pairs wait up to 240 seconds for scene readiness before freezing
parking records, preventing incomplete cold-load manifests from becoming baselines.

Authored articulation: `authored.ts` retains full far meshes and caches separate near bodies,
glass and four hub-centred wheel parts from the named `wheel_*` nodes. `wheelLayouts.ts`
shares axle offsets and tyre dimensions with ground probes. Wheel design batches
share identical baked geometry, while dynamic and remote cars use the same four parts.

Converted source cars: `game/scripts/import-fleet-gltf.mjs` turns permissively licensed GLBs (CC BY 4.0, listed in
`tools/dev/fleet-import/models.json`, sources and their licence pages outside git in `~/turku3d/loops/carmodels/<id>/`)
into the same articulated LOD format. It maps source materials to fleet roles (tintable paint, fixed liveries for
taxi/police/ambulance/bus/truck, glass, lamps, indicators, blank plates), samples base-colour textures into vertex
colours (no images ship; `flattenFleetAsset` multiplies them), drops emblems (small centreline parts at the nose or tail,
plus per-model boxes), orients to +Z/Y-up metres, splits four hub-centred wheels (front and rear-most axle; middle axles
stay in the body; skinned CARLA sources split by their `Wheel_*` bones) and simplifies with meshoptimizer to about
32k/8k/3k triangles (60k/12k/4k for trucks and buses). Normals and colours ship as 8-bit. CARLA emblems are modelled into
grilles, so `emblems.extra` boxes drop or flatten them (`flat` presses relief onto one plane with the box's median colour).
`tools/dev/fleet-import/licenses.py` regenerates the LICENSES rows. Registry entries add the
source/licence, a collision box, a carphysics `CarSpec` and a selection `weight`. Families outside
`SELECTABLE_FAMILIES` (bus, truck, ambulance) are not preloaded for traffic and need an exact model id.
`vehicle-gallery.html` shows and drives them.

True size and parking slots: every model is drawn and simulated at its real dimensions. `fleetModel(model, seed, slot)`
offers a model for a parked record only when its overhang beyond the record box fits the smaller of `clear_front_m`/`clear_rear_m`
minus 0.3 m (`fitsSlot`), or returns null (an empty slot); moving traffic has no slot. Parked collision boxes use the
model's own length.
Generated liveries (`generatedLivery`): `police-fi` (white, blue belt band, blue/yellow check row, roof light bar;
flank triangles are subdivided first) and `taxi-fi` (yellow roof sign). No logos or text.
