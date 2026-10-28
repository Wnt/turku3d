# Facade reference selection and held-out truth

2026-10-28, `cvr-references-truth`, M1 Max. Detailed crops, reviewer outputs and per-wall
predictions stay under this task's `$TURKU3D_WORK/reports/`; this report contains aggregates only.
The two independent high-effort Astra reviewers saw wall-plane crops and target outlines but
not each other's answers. A usable reference had to show the intended wall end to end and
base to eaves, with one facade plane and at most 20% foreground obstruction.

## Reference selection

The reference stage now applies the panorama's upright pose and true-to-grid heading, checks
the LOD2 target plane and neighbouring geometry along rays to the full wall, and trims an
overestimated roof/gable height to supported eaves. It screens foreground classes and
detected trees, people, vehicles, fences and scaffolding separately from sky, road and
sidewalk. A view with over 20% foreground coverage is rejected. The selector tries other
panoramas and sources if its candidate fails, and ranks accepted views by a clear/moderate
occlusion band before source and geometric/photo score. The band avoids treating small,
noisy detector-score differences as proof that one moderate-occlusion view is better. It
also samples a full wall from each available imagery source before spending view slots on
details. A local four-view probe recovered a complete wall that the previous two-view
research run missed because a geometrically higher-scored but cropped view had passed
the automatic pixel screen.

On the same 100 legacy candidate walls, automatic reference support increased from 58 to 66.
Paired independent review found **5/100** usable before and **8/100** after. The automatic
count is a pixel/geometry screen, not a substitute for this paired usability measure.
On the fixed 425-wall inventory the automatic screen increased from **183 to 245** supported
walls, but independently agreed usability fell from **34/425 to 22/425**. Its first reviewer
inspected all 245 presented references. The second reviewer inspected every first-reviewer positive;
a first-reviewer rejection cannot pass the two-reviewer AND rule. This short circuit
preserves the exact paired usable count while avoiding calls on already rejected crops.
The 425 inventory contains 265 segments under 4 m and 338 under 8 m. On its 87 segments
at least 8 m wide, agreement was 10 before and nine after; most of the measured loss is on
narrow mapped strips whose boundaries cut window bays. Direct inspection also found some
near-identical before/after crops with changed reviewer judgments, so the paired count is a
noisy measure at these small totals. The reference selector cannot recover a complete physical
facade by padding a crop beyond the supplied segment. The original 425-wall inventory and
the 100 legacy candidates are kept fixed for the paired comparisons; the held-out truth
inventory instead derives connected physical wall surfaces from LOD2.

I also tested replacing only unambiguous legacy segments with connected coplanar LOD2
wall extents. Strict orientation, plane-distance and overlap checks matched 53/425
segments across 17 buildings, expanding their median width from 6.74 m to 25.46 m.
After fresh reference selection and independent review, this variant presented 238/425
and passed **21/425**, versus 245 and 22 for the unexpanded v12 set. The changed crops
often still lacked a visible base or eaves, or had foreground obstruction. I did not
deploy the physical expansion because it reduced the measured usable share.

## Held-out wall protocol

The local candidate inventory merges connected coplanar LOD2 WallSurface runs and uses
their supported eaves and N2000 base. It excludes buildings in the original 425-wall set.
Selection keeps one street-facing, full-wall crop per building, with at least 8 m of width;
the widest independent candidates are taken first. The refreshed primary pool contributed
66 agreements, an 80-wall reserve contributed 26, and the first 20 reviewed walls from a
120-wall wider regional contingency contributed eight. The final 100 have 100 distinct
building IDs and no overlap with the 425-wall inventory. Widths are 8.36–53.89 m, median
21.40 m. Every final label matches the exact crop and outline hashes seen by
both independent high-effort `gpt-6-astra` reviewers. They judge wall identity, complete
extent, foreground obstruction, storeys, bays, cladding and colour without seeing each
other's response. Only matching usable labels enter the field evaluation. An independent
low-effort Astra pass describes the same accepted crops for comparison. These are
model-consensus labels, not human ground truth.

## Evaluation protocol

The frozen feature extractor runs on the exact accepted crops. The production draft's
optional OSM and register enrichment is applied to the LOD2 ground footprints before
scoring the register fallback. A five-fold held-out comparison excludes each test building
from both numeric-head fitting and material-source selection. The historical clean pilot
walls are training only. Per field, the register fallback and hybrid are scored against
the same independent agreement; a separate
low-effort Astra call sees the accepted crop and supplies a third prediction. Storeys,
window columns and material use exact match; wall colour uses median CIE76 distance in
Lab. A field is eligible for the background queue only if the hybrid improves on the
register with complete coverage across all 100 held-out walls. Abstentions retain the
register value and are counted separately.

| Field | Register | Hybrid | Astra low | Background hybrid |
| --- | ---: | ---: | ---: | --- |
| Storeys, exact | 66/100 | 60/100 | 98/100 | No |
| Window columns, exact on multi-storey walls | 10/64 | 9/64 | 56/64 | No |
| Cladding material, exact | 63/100 | 83/100 | 91/100 | Yes |
| Wall colour, median CIE76 ΔE | 15.92 | 9.68 | 2.72 | Yes |

All 100 held-out walls had predictions for every scored field, with no hybrid abstentions.
Optional register enrichment changed wall colour attributes on 33 truth buildings,
ground-floor status on 28, levels on 16 and material class on 10. The WFS-only
fallback scored 65/100 storeys, 53/100 material and 15.83 colour ΔE; the winning
fields are unchanged. The shipped parameter record embeds the enriched evaluation.
The five-fold fit used 72 older clean walls as training-only data. Material-source choice
was nested inside each training fold; all five folds chose the embedding head. Hybrid
feature extraction averaged 1.43 s per held-out wall on the M1 Max, excluding reference
acquisition and model warmup. The queue keeps register values for storeys, columns and
trim colour; it applies only measured hybrid material and wall colour, then refines the
most observed or nearest walls with Astra when its configured call budget permits.

## Queue observation

A fresh, isolated ring/cone queue run with the fitted head and four-view reference
selection upgraded three buildings in 34.289 s, or **5.25 hybrid buildings/minute**.
It made **0 Astra calls in the observed hour** because weekly usage was above the
queue's 60% pause threshold; the configured ceiling is **12 calls/hour**. An earlier
under-threshold queue probe recorded one actual Astra invocation and a separate
cached upgrade, but used the pilot head and is not included in the final throughput
rate. The final three-building run recorded zero unknown-call jobs and three draft
refresh requests. These numbers include reference acquisition and warm-model startup
on this Mac, but the panorama tiles were already in its local cache.
