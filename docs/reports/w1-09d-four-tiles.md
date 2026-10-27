# W1-09d continuation: four L0 street-facade tiles

Date: 2026-10-26. Sandbox: `w1-09d-facades`. The branch was rebased onto
current `main` before work. The authoritative buildings inventory contains 491
street-facing facades whose midpoint falls in `t0_0_0`, `t0_0_1`, `t0_1_0` or
`t0_1_1`. The task started with 115 bound and 376 unbound. Input features were
sorted by distance from the office spawn using `turku3d.config` coordinates.

## Method

The default path uses existing rectified panorama pixels, depth masking,
same-facade bay inpainting, exposure correction and narrow library plinth and
roof-edge bands. It publishes albedo, normal, ORM and sidecar metadata under
the additive assembly lock. The prior stopped partitions supplied 128 exact
geometry-matched reference folders; these were copied into this sandbox for
pixel reuse. Other walls were searched in the shared Google panorama cache;
new cache writes stayed under this sandbox.

For the 24 earlier visually rejected walls, plus the previously aspect-failed
office wall and four no-view walls, a separate reference pass queried Google,
Look Around and Mapillary. A second pass queried older Street View dates.
`facade_alternates` records a selected full-wall view by ID in `preferred.json`
without modifying the original panorama score or pixels. The same grouped
`gpt-6-luna` low-effort visual labels gate the direct path. No image improve
call was made in this continuation. Every Codex runner checks the account-wide
weekly percentage before each call and stops at 70%.

The selected references, per-facade previews, reasons for unbound walls and
distance ordering are in
`$TURKU3D_WORK/reports/facade-contact-four-tiles.html` and its adjacent JSON.
The per-facade KTX2 and sidecars are in
`$TURKU3D_DATA/textures/assembly/facades/`; no panorama pixels are committed.
Exact repeat commands are in [textures/README.md](../../textures/README.md).

## Coverage and cost

All 376 initially unbound street facades received the default path in distance
order, and the panorama search completed for every wall in the four tiles.
The final manifest binds 316 of 491, an increase of 201 from the initial 115.
Within 120 m of the spawn, 116 of 129 are bound; all 13 remaining walls have
an explicit rejection reason. Across the four tiles, 110 unbound walls have a
visual-label rejection and 65 have no usable full-wall panorama view. The
contact JSON lists all 175 individually and has no unreasoned or pending row.

Alternate Mapillary, Look Around and Street View panoramas recovered 13 of the
24 earlier visually rejected walls. The previously aspect-failed office wall
was also recovered from a Mapillary view. Three further walls were recovered
from older Street View dates. This continuation made zero image-improve calls,
so the additional 10-wall cap was untouched.

The `scripts/dev/codex-cost` ledger records 44 grouped `building_label` calls
in this continuation: 1,157,194 input and 27,213 output tokens, 1,184,407
total. They assessed 322 facade images, including alternate assessments of the
same wall, or 3,678 tokens per assessment. The direct path used zero Codex
tokens. Weekly quota was 41% at start and 47% at the final snapshot; it is
account-wide and includes other agents' work.

## Verification

The selected quality gate covers Python lint, policy and texture tests. The
manifest audit checked all 316 bound facades, all three KTX2 hashes and headers
per facade, and authoritative dimensions to 5 mm: zero errors. Four sidecars
from the pre-existing binding set round width or height to the nearest
millimetre; every new direct sidecar retains the exact source dimensions.
