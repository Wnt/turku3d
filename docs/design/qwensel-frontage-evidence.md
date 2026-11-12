# Qwensel river frontage

The existing landmark description for building103295888D already records six
irregular windows and a raised double doorway between the fifth and sixth.
Four southwest windows have open pale diagonal-board shutters and fine glazing
bars; the two nearest the entrance have six larger panes. Its generic spacing
parameters were rendered as eight evenly spaced openings, losing those observed
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

The raised entrance is now constrained by a bounded private point-cloud study.
Classes1,2,12 were requested and class6 retained as a supplementary building/deck
check. The local selection contains class2 ground and class6 structure, with no
class1/12 returns. Twelve class6 last returns form a compact platform strip at
H5.169–5.227m N2000, connected to descending flights on both sides. Sparse higher
rail points are distinct from that profile. The platform is authored at H5.20m
with approximately±0.05m uncertainty. The9mm plane residual over a narrow strip
is not the total survey/registration uncertainty or evidence of full deck depth.

The threshold shifts0.25m along the retained wall relative to the initial photo
prior. Independent class2 ground fits near the two flight toes use six returns
each within0.4m, giving H3.823/3.816m (RMS6.3/1.2mm). Two post regions use10/9
returns, giving H3.827/3.803m. Ground contacts are rounded to0.01m with±0.03m
source uncertainty. Seven risers per flight (six treads plus the landing),1.2m platform width,1.6m runs and0.9m
depth remain construction/profile priors; the small point strip does not measure
every tread or the full platform outline. Resulting risers are about0.197m.

`qwensel_entrance_details()` adds closed treads, risers, stringers, supporting
posts and crossed rails as solid meshes; door mouldings are passable ornaments.
`qwensel_entrance_contacts()` exposes four actual toe/post contact centres and
footprints for the producer to compare against its current terrain. The source
survey does not itself prove agreement with a subsequently compiled ground mesh;
that remains an integration and deployed foot-contact check.

Generate the owned asset without reference images:

```sh
PYTHONPATH=pipeline python -m turku3d.scene.qwensel_maps WORK/qwensel
node pipeline/turku3d/scene/qwensel_appearance.mjs ORIGINAL.glb WORK/qwensel/maps.json WORK/qwensel/qwensel.glb
```

The producer imports the adaptedGLB and three immutable map files, keeps the
original approved source hash in provenance, and batches `qwensel_window_details()`
with `QWENSEL_MATERIALS`; add `qwensel_entrance_details()` and audit its explicit
contacts against current ground before activation. Dedicated normal-gameplay before views were captured on207e6a88 at720×480
internal resolution, with fixed world transforms recorded for the after pair.
The wide view is partly tree-occluded; oblique and close views show the frontage.
Activation follows this baseline; input generation and unit checks alone are not a visible milestone.
