# Foliage on measured hedge volumes

The mapped hedge centre lines, surveyed crown heights, deliberate path openings
and four supported feet remain the physical authority. `hedge_foliage.py` replaces
only their flat visual boxes with the retained nature library's leaf-card grammar.
Eight deterministic seed variants are terrain-warped into each measured segment;
normal vectors use the inverse deformation Jacobian. Adjacent segments overlap
at internal joins, while true line ends and path openings trim all leaf reach.
The lateral and top shell is at most5 cm, inside the source's15 cm uncertainty.
Species, trim details and individual leaves remain appearance priors.

The unchanged compressed atlas comes from the existing CC0 ambientCG LeafSet002
foliage library. Its content hash is verified before copying. No photo pixels,
new texture generation, per-building characterization or runtime geometry
inference are required. The producer and retained recipe hashes identify the
appearance source; normal recipe compilation reproduces the output.

Walking collision and optical blockers have separate roles. The broad physical
hedge hull encloses most leaf cards; using it as an opaque light blocker made
those visible leaves black at night. The explicit `lightOcclusion` mesh field
allows `solid` or `none`, defaulting to the physical solid policy when absent.
Each hedge keeps its original collision triangles and disables only that hull's
optical blocking. A hidden passable proxy reuses the exact three opaque faces of
the deformed visual inner core. Other walls, posts and all independent blockers
remain active. This is a bounded opaque-core approximation of foliage, not a
measurement of leaf transmission. No brightness multiplier or additional
per-frame lighting pass is introduced.

The source checks exercise sloped envelopes, finite unit normals, deterministic
joins, trimmed openings, unchanged collision geometry and the independent
optical blocker. Native day/night inspection and a walking check qualify the
actual scene; geometric checks alone do not establish an appearance improvement.
