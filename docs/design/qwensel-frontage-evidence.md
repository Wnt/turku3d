# Qwensel river frontage

The existing landmark description for building103295888D already records six
irregular windows and a raised double doorway between the fifth and sixth.
Four southwest windows have open pale diagonal-board shutters and fine glazing
bars; the two nearest the entrance have six larger panes. Its generic spacing
parameters were rendered as seven evenly spaced openings, losing those observed
relationships. The correction makes this existing evidence explicit.

The main source is [Qwenselin talo — apteekkimuseo — Turku01](https://commons.wikimedia.org/wiki/File:Qwenselin_talo_-_apteekkimuseo_-Turku_01.jpg),
photographed10 July2024 by VaittinenTimo. The already retained opposite-oblique
Commons reference62757339 independently confirms the shutter construction.
Private calibrated2019 panoramas confirm the frontage order with shutters
closed, but small oblique correspondences have roughly0.8–1.0m ray residuals;
they are not treated as surveyed opening centres. No reference pixels ship.

The clear2024 photograph shows sixteen panes per shuttered opening (four
columns by four rows) and six panes per entrance-adjacent opening (two by three).
Manual projective registration of frontage proportions uses the retained game's
two wall segments, total approximately22.5m. Opening centres, sill levels and
sizes are explicit priors with approximately±0.4m uncertainty. The game's wall
height and datum are retained rather than inferred afresh from a photograph.
Colours reuse the original AI material parameters and are not photometric truth.

`qwensel_frontage.json` records the reviewed layout, original source hash,
authored wall vertices and retained material parameters. `qwensel_appearance.mjs`
selects exactly four triangles (the two river-wall quads) by their retained
normals and plane location. It splits their material assignment, adds metre-space
frontage UVs and keeps every original binary byte, position, normal, other wall
UV, roof material and original image dependency. Three generated KTX2 maps add
the explicit layout. Both the source asset hash and the exact layout hash must
match; mutable map references are rejected.

`qwensel_maps.py` reuses the existing procedural timber, rubble-stone and
elevation generators. `qwensel_details.py` adds six shallow timber window frames
and four pairs of diagonal-board shutters, with recessed joints and iron hinges.
These are passable ornament meshes; the retained building collider owns the wall.
The shutter thickness0.035m, board pitch0.14m and shallow open pose are construction
priors. Attachment follows each actual wall segment, not a single guessed plane.

The raised door remains a qualified layout prior. Supported entrance stair
placement requires a separate ground/landing check: the old AI sill estimate and
photographic registration differ, and the current promenade surface is being
refined independently. Do not infer walkable stair support from this texture.

Generate the owned asset without reference images:

```sh
PYTHONPATH=pipeline python -m turku3d.scene.qwensel_maps WORK/qwensel
node pipeline/turku3d/scene/qwensel_appearance.mjs ORIGINAL.glb WORK/qwensel/maps.json WORK/qwensel/qwensel.glb
```

The producer imports the adaptedGLB and three immutable map files, keeps the
original approved source hash in provenance, and batches `qwensel_window_details()`
with `QWENSEL_MATERIALS`. A dedicated normal-gameplay before capture must precede
activation; input generation and unit checks alone are not a visible milestone.
