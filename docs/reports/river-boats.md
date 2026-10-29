# Moored Aura boats use a boat producer

The seven central mapped ships previously entered the house producer, producing
plaster facades, domestic windows/doors and thick extruded mast footprints.
The new prepared `boats` dataset matches complete OSM `building=ship`, ship/boat,
floating and hulk footprints with city building parts. Strongly water-covered,
elongated low survey footprints also qualify. Land buildings remain ordinary
buildings. Matched source facade identities are retained in boat provenance;
existing facade descriptions, facade-kit outputs and texture files are not edited.

Boats use their mapped pointed outline, a dark flared hull, a shallow waterline
paint band, an open deck, city-measured cabin roof outlines/levels, compact dark
boat windows, blue horizontal trim and rooftop decks. Small high roof parts
become 11 cm diameter masts instead of thick chimney-shaped solids. White canvas
parasols, tables, seats and open blue/white rails provide restaurant furniture.
Solid rail proxies keep collision small while the full visual retains open bars.
The primary Donna fragment uses roughly 550 solid triangles; the four affected
tiles have approximately 200–3,700 solid boat triangles each.

Names from source: Donna, Svarte Rudolf, Papa Joe, Ravintolalaiva Cindy, Lulu,
M/S Katarina and Chibi Chill Majland. Owners are `t0_0_1`, `t0_-1_1`, `t0_-2_2`;
Donna's bow also extends into `t0_1_1`. Complete source features generate all
fragments; visual and collision tile clipping retain shared geometry.

Private references are `~/turku3d/briefs/img/river-boat-43.webp`, `-44.webp` and
`-45.webp`. The local WebGL component preview is in the sandbox
`reports/boat-preview-side.png` / `boat-preview-overview.png`. No private capture
or reference imagery is committed. Geometry retains source measurements; furniture,
paint families and window rhythm are generic boat details, not a reconstruction
of every individual restaurant fitting.

Selected checks:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_boats.py pipeline/tests/test_coarse_sources.py pipeline/tests/test_coarse_footbridge.py
python3 scripts/dev/probe_boats.py --commit "$(git rev-parse HEAD)"
```

The new tests cover source ship/land classification, preserved source facade IDs,
measured cabin/mast heights, full deck area across a tile seam, material namespaces
and bounded collision. The selected deployed probe builds first/full on the four
affected tiles, compares building/collision counts with source, audits all seven
boat identities and confirms they are absent from ordinary house facades.

An additional existing first-pass publication test passed its artifact and immutable
publication assertions, then failed the old `Builder.request == queued` expectation.
Current main's `request_journal.request_tile` deliberately returns `built` when a
retained first visual exists. This unrelated serving assertion is reported to
coordination and is not part of the selected boat gate. The deployed first/full
probe supplies the publication evidence for this change.
