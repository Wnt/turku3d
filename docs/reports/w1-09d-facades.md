# W1-09d: direct office-core facades

Date: 2026-10-26. Sandbox: `w1-09d-facades`. The authoritative buildings
inventory contains 129 street-facing facades whose midpoint lies within 120 m
of the office spawn. The shared assembly began this task with 48 total bindings,
33 of them inside that radius. The nearest-first task inventory preserves the
buildings GeoJSON geometry exactly and contains the other 96 walls.

## Production path

`turkutex.facade_direct` uses the best full-wall rectified Street View or Look
Around crop. Its depth mask excludes known nearer surfaces, holes are filled
from visible repetitions on the same wall, broad exposure is matched, and the
existing plinth and roof-edge library images supply narrow trims. The crop is
scaled to authoritative metres, encoded to glTF-valid albedo, normal and ORM
KTX2 maps, and published per facade with geometry and source hashes. The
assembly manifest is updated under the existing export lock. Failed walls stay
on their library fallback and receive a reason in the contact sheet.

The prior partitions' locked additive export was integrated here. Compressed
panoramas stay in the shared atomic cache; decoded RGB arrays are reused from
read-only memory maps under this sandbox's `cache/streetlevel-decoded`. The
reference search was resumed after one slow Look Around HEIF decode and
completed the last eight walls with Google-only references. No raw panorama
pixels are committed to the repository.

`turkutex.facade_labels` grouped up to eight outlined walls in each
`gpt-6-luna` low-effort call. A label marked 27 crops unusable, including
scaffolding, tree cover and incorrect views; those direct textures are not
bound. Three rejected walls lay within 60 m and received the targeted Codex
image improve pass. Two passed the original aspect and KTX2 validation and
were bound. `103295893K_33` still failed the generated-content aspect check
(49.2% error against the 30% limit) and remains on its fallback. There were
three image-generation calls, below the task cap of 25. Every model call used
the runner's 70% weekly-quota check.

## Coverage and evidence

The final manifest binds 115 facades across the AOI, including 100 of the 129
street facades within 120 m. Sixty-seven bindings were added by this task over
the starting 48. Within 60 m, 34 of 35 are bound; the remaining wall is the
aspect-rejected `103295893K_33`. All 29 unbound walls within 120 m have a
per-facade reason: 25 visually rejected reference crops and four without a
usable full-wall panorama view. All unbound walls keep library fallbacks.

The final `$TURKU3D_WORK/reports/facade-contact.html` contact sheet shows the
reference and texture preview for each wall, in distance order. Its adjacent
`facade-contact.json` lists every unbound facade and its reason. The
per-facade KTX2 and metadata files are in
`$TURKU3D_DATA/textures/assembly/facades/`. Selected inventory, reference
crops, label responses, model call logs, and rejected outputs are in
`$TURKU3D_WORK/textures/` for this sandbox.

## Codex cost

The `scripts/dev/codex-cost` ledger records 12 grouped label calls:
311,515 input and 7,490 output tokens, 319,005 total. Dividing by 92 labelled
walls gives 3,467 tokens per facade. The CLI's automatic per-facade column shows
4,197 because the first two resumed batches reused job IDs; later batches use
facade-based IDs, and the unduplicated call totals above are authoritative.
The direct photo/cleanup path used zero
Codex tokens. Three improve calls used 309,342 input and 2,718 output tokens,
312,060 total, or 104,020 per attempted facade. The task spent 631,065 total
tokens across these steps. Weekly quota was 38% at task start and 41% at the
end. This percentage is account-wide and includes other active agents.

## Verification

The final selected quality gate passed in 7.149 seconds (`generated` cached):
`python-lint`, `policy`, and `textures-tests` were green. An earlier uncached
run of the same area passed in 14.606 seconds. Focused tests
cover repeated-bay inpainting and masking, the additive export lock, grouped
labels and repair distance/call cap, and exact decoded panorama pixels.
An independent manifest audit checked authoritative width/height and all three
KTX2 hashes for the 100 bound walls within 120 m: zero failures.

Exact production and resume commands are in [textures/README.md](../../textures/README.md),
under “Direct photo path for the office core”.
