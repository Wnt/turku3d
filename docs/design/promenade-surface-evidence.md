# Bounded promenade surface finish

The main office promenade already uses the canonical `road` surface and its
asphalt material. A recipe's separate `footway` and `quay` aliases do not describe
this particular patch. The finish adapter retains that asphalt and all original
geometry rather than replacing the whole promenade's material.

Two private registered 2019 panoramas show a reddish granite-sett cross-band
beside canopy fixture 56200764, with a narrow grated drain inside a wider stone
surround parallel to the facade. The 2023 opposite-oblique Qwensel photograph and
[July 2024 Qwensel photograph](https://commons.wikimedia.org/wiki/File:Qwenselin_talo_-_apteekkimuseo_-Turku_01.jpg)
corroborate the permanent asphalt/sett/drain arrangement. The office was under
facade restoration in 2019; that temporary wall appearance is not reproduced.
Reference images stay private and are not used as textures.

`promenade_finish.LAYOUT` records the approximately 6.4 m cross-band segment and
approximately 4.7 m drain segment visible in the registered close view. The
cross-band continues to the mapped hedge-front intersection at engine
[177.8979, 264.0565], producing an approximately 11 m run. The drain continues
along the retained office facade endpoints for approximately 39 m. These
continuations are explicitly inferred; the originally observed segments remain
separate fields. No new repeated cross-bands are added. Manual
pixel rays were intersected with the current supported ground, then aligned to
the retained facade direction. The camera-to-map fit is approximate, with
0.8 m positional uncertainty; it is not a survey. A second viewpoint supports
the association but does not justify centimetre precision or periodic repetition.
The roughly 0.6 m band/surround widths have about 0.15 m uncertainty. Individual
0.12 m setts, staggered joints, 0.04 m grate pitch, iron finish and muted rose-grey
pigment are explicit construction and appearance priors, not photometric truth.
The existing granite material maps are reused; no new source pixels are shipped.

`surface_inlays.drape` clips bounded XZ polygons against existing canonical
receiver triangles. It interpolates their normals, ambient visibility and night
irradiance, preserving the receiver's actual height and light field. New meshes
are passable and reference the existing support. They do not become supports or
colliders. Optical lifts of 2–4.5 mm separate the visible finish layers; all
original geometry, support records, collision files, fixtures, assets and their
lighting arrays remain exact. Height filtering prevents a lower-level marking
from being placed on a bridge above it. Polygon holes are respected.

The reusable helper also accepts explicit source road-marking polygons. The
promenade adapter deliberately does not synthesize a complete band grid or claim
that every street has this finish. Its initial private build on A3 retains all
6,369 existing meshes and 20 collision files and adds 19 passable meshes containing
5,791 triangles. Visual acceptance requires matched ordinary gameplay captures.

Selected helper tests:

```sh
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m unittest discover -s pipeline/tests -p test_surface_inlays.py -v
```

`compile_scene` applies the finish whenever the office asset is present, so an
ordinary recipe build reproduces it. The former post-hoc `promenade_finish`
publication command was removed: it added geometry to an already compiled scene,
which is not an appearance-only change. Finish geometry changes go through a
recipe build; material-only changes go through `python -m turku3d.scene.appearance`.
