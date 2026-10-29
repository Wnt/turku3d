# Learned facade rules and painter coverage

2026-10-29. Read-only inventory, building-held-out prediction evaluation and production painter audit. No screenshots, driving checks, image generation, rejected-wall analysis or area builds.

## Evidence and denominators

The immutable `publications/ring42/summary.json` reports 1,042 characterized walls, 306 painter-eligible and 736 deferred. The actual `programs.json` contains **648 buildings and 1,049 walls**: 13 earlier pilot walls, 301 structured row/bay forms and 735 deferred section descriptions. One earlier pilot wall remains unreviewed. These are different snapshots/retention rules; this report does not silently substitute one denominator for the other. All batch answer files contain 1,080 unique accepted wall IDs and 1,346 accepted answers, including 266 repeat/revision answers. Those are inventoried but not added to the frozen publication training set. Rejected records are skipped immediately.

Every published wall was inspected for supported target fields. **465 buildings** provide at least one usable empirical target; unknown fields do not become labels. The complete model and per-wall inventory remain private, outside git. Source SHA256: `bd5bb74b4e469a30e797d76875f2d44ea89b3679433d9599db5d63b6f978af95`.

## Learning and neighbourhoods

The fixed split holds out **130 buildings (20%)**, with all their walls together; 518 buildings form the training partition. The split uses SHA256 ranking of stable building IDs and was fixed before scoring. Fit stores one empirical distribution per building, so repeated walls do not increase that building’s vote. The final deployment model is subsequently refitted on all available published observations.

The 32 nearest covariate neighbours define the conditional prior. Covariates are register construction year, use, storeys, footprint area/compactness, frontage, cadastral district, street class and corner position. A second prior adds readings within 250 m, weighted by distance and year difference; same street/block readings receive twice the local weight. Unknown years weaken local propagation. Inferred facades never become donors. Runtime sampling uses independent stable building/field seeds. Numeric dimensions shrink 75% toward the conditional mean and retain a 25% sampled variation; opening multiplicity remains discrete. Existing maps, descriptions, photographic programs, register storeys and calibrated pigments retain precedence.

Cached context enrichment supplies footprint/use/district for 636 buildings, street class/corner context for 634, construction year for 629, and register storey values for 642. All 648 have frontage and position; 384 have street names in the source attributes. Missing features are omitted from distances. District/block data come from cadastral IDs, not inferred social or stylistic labels.

## Held-out predictions

Categorical entries are accuracy (higher is better); numeric entries are mean absolute error (lower is better). Scores average walls within each held-out building, then buildings. “Seeded runtime” uses the deployed sampling estimator, not just the more accurate mean/mode predictor.

| Field | Metric | Buildings | Authored | Conditional | + neighbours | Seeded runtime |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| balcony | accuracy | 60 | 0.7556 | 0.9278 | 0.8944 | 0.8611 |
| bay_m | MAE | 50 | 1.4436 | 1.2420 | 1.1945 | 1.3168 |
| cornice | accuracy | 39 | 0.3718 | 0.5342 | 0.5085 | 0.5085 |
| course_share | MAE | 39 | 0.3722 | 0.2095 | 0.2068 | 0.2118 |
| ground | accuracy | 51 | 0.3105 | 0.6863 | 0.6569 | 0.4118 |
| ground_scale | MAE | 39 | 0.1191 | 0.0898 | 0.0841 | 0.0941 |
| head | accuracy | 54 | 0.6852 | 1.0000 | 1.0000 | 0.9630 |
| material | accuracy | 74 | 0.2973 | 0.5518 | 0.5518 | 0.4054 |
| ornament | accuracy | 52 | 0.4135 | 0.5769 | 0.5962 | 0.5481 |
| rhythm | accuracy | 51 | 0.7647 | 0.9510 | 0.9510 | 0.9118 |
| shop_share | MAE | 34 | 0.3799 | 0.1500 | 0.1502 | 0.1697 |
| surround | accuracy | 49 | 0.4694 | 0.6735 | 0.7143 | 0.6122 |
| top_scale | MAE | 39 | 0.0907 | 0.0985 | 0.0926 | 0.0885 |
| window_height | MAE | 50 | 0.0707 | 0.0247 | 0.0235 | 0.0244 |
| window_width | MAE | 50 | 0.0891 | 0.0639 | 0.0611 | 0.0649 |
| windows_per_bay | MAE | 50 | 0.0240 | 0.0351 | 0.0404 | 0.0240 |

Bay width is in metres, from complete counted bays and the known wall frontage. Ground/top scales are qualitative row heights relative to a uniform storey. Window width/height are **qualitative opening-class layout fractions**, not measured photographic dimensions. Shop/course shares and multiplicity use only explicit counted forms. Partial descriptions contribute explicit material/head/surround labels and positive balcony/shop/ribbon evidence; absence of a mention is not an observed negative. The evidence does not justify pixel-size or photographic-fidelity claims.

The seeded estimator improves 15 fields and ties 1 in this holdout. Neighbour propagation improves several numerical/detail fields, but reduces balcony, cornice and ground-type point accuracy versus conditional-only prediction. Rare heads and balconies are strongly imbalanced; a high accuracy does not establish reliable rare-class performance. No parameters were tuned against this holdout.

## Painter coverage

| Scope | Before | After |
| --- | ---: | ---: |
| Published reviewed full/strip programs | 313 | 313 |
| Deferred walls with bounded partial support | 0 | 386 |
| Deferred walls without a renderable bounded section | 735 | 349 |
| Earlier unreviewed pilot wall preserved | 1 | 1 |
| Actual published walls compiled by the audit | 313 | 699 |

The adapter produces 461 supported bounded sections. **Partial support is not full-wall rendering**: incomplete opening counts, hidden regions and arbitrary prose are not promoted to complete wall grids. Sections use named-region layout priors, with provenance; they are not newly measured rectangles. Existing stored maps remain bound on partially characterized walls. Unresolved regions retain existing treatment.

All observed finite structured enums are supported: six bay kinds (window, door, shop, blank, arched portal, unknown), four heads (flat, segmental, round, pointed), four surrounds (none, frame, pediment, rustication), seven materials, four ornament levels, four cornice levels, base/middle/top roles, short/regular/tall row heights, small/regular/wide/tall opening classes, regular/ribbon/paired rhythms, multiple windows per bay, string-course boundaries and positioned balcony rails. Opaque service doors are a new distinct kind. `unknown`/null means preserve or abstain, never paint a guessed opening. Ornament levels now produce bounded trim details; their shapes remain priors.

The original row compiler already supported most enums. This change connects safe deferred sections, renders ornament levels, distinguishes opaque service doors from glazing, and expands learned multiplicity/rhythm/surrounds/balconies through the same painter contract. It does not claim that all 6,409 distinct free-text leaf values have become renderer primitives.

Still unsupported or deliberately unresolved: exact clock/sign/mural artwork; ambiguous current/historical wall association; clipped or occluded opening counts and missing section boundaries; offset projecting/recessed facade planes, glazed balcony volumes, loggias, verandas, external stairs, colonnades and through-passages. These require bounded observations or separate geometry, not a flat-window substitution. The private inventory lists every affected wall/section. Thus the requested near-total deferred-wall coverage is **not achieved** by this slice.

## Verification and deployment

After the wall-compositor rebase, the production painter audit compiled 699 published walls and 648 synthetic unseen-building layouts in 122.572 s; all coordinates/instructions were finite and vertically bounded. The final post-deploy audit is recorded in the task receipt, and checks the frozen deployed compositor too. Regular learned layouts reuse the existing shader attributes; only spatial exceptions expand to partitioned patches.

```sh
source ../sandbox.env
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_learning.py pipeline/tests/test_facade_structure.py \
  pipeline/tests/test_facade_programs.py
PYTHONPATH=pipeline:textures scripts/dev/codex-task wait-cmd -- \
  python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python \
  tools/research/facade_characterization/learn.py
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python \
  tools/research/facade_characterization/audit_painter.py \
  --model "$TURKU3D_WORK/reports/facade-learning/facade-rules.json" \
  --out "$TURKU3D_WORK/reports/facade-learning/painter-audit.json"
```

Private products are under `$TURKU3D_WORK/reports/facade-learning/`: `inventory.json`, `context.json`, `evaluation.json`, `facade-rules.json`, `painter-audit.json`. The runtime model lives in the OCI work root as `facade-rules.json`, away from the public asset index. It contributes to appearance parameter hashes; structural geometry/collision caches remain reusable. Code ships through Pages and the OCI source deployment. Tiles regenerate only on demand. No existing facade image/asset or characterization intermediate is replaced or deleted.
