# Finnish vehicle fleet

`catalog.ts` defines 56 original fictional models: seven designs each of hatchback,
estate, sedan, SUV, MPV, delivery van, pickup and older sedan. A stable hash selects
models within the existing parking/traffic body families. Ordinary cars dominate;
pickups and older sedans have lower selection weights. These weights are art
direction, not asserted registration statistics.

`geometry.ts` generates a merged mesh at three levels of detail. Bodywork has real
wheel openings, a tapered glasshouse, pillars, seals, panel seams, mirrors, grilles,
lamps, wheels, plates and occasional accessories. Meshes use +Z forward, Y up,
metres, with tyre contact at Y=0. The visual layer scales parked replacements to
the authoritative parking record's bounds. Physics, traffic flow and suspension
remain separate.

`visuals.ts` keeps one instanced draw per visible model and LOD. All draws share one
node material, including opaque reflective glass, tyres, metal, paint and lamps.
Paint and lamp state are instance attributes. Moving/driven vehicles power their
lamps; parked cars do not. LOD boundaries are 42 and 105 metres; vehicles beyond
320 metres are culled. Only the nearest LOD casts shadows. All replacement draws
are prepared before the old batches are released.

In a local development server, open `/fleet-gallery.html` for the model workshop.
Its `window.fleetWorkshop` supports `show(index, lod, colour)`, `grid()`, `pose()`
and `night()`. To inspect real parking in an existing fixed-camera game page:

```js
await import('/src/actors/fleet/qa.ts').then(m => m.installFleetQA());
await window.__game.fleetQA.refresh();
window.__game.fleetQA.snapshot();
```

The hook uses real manifest slots, occupancy and terrain rotations; it does not
invent parking locations. Call `refresh()` after moving to a new loaded area.

All geometry and details are authored in code for this project. No GTA, Google,
manufacturer model, logo or panorama pixels are included. Real street references
and statistical source notes stay outside git in `~/turku3d/data/research/cars/`.

The cabin side is a connected painted surface with separate inset front, rear and quarter
windows, seals and flat pillars. Tyres use rounded shoulders and recessed rim barrels;
a coarse alloy pattern remains at the middle LOD. Near plates use original fictional
Finnish-style letter/number strokes. All detail remains in the shared merged geometry.
