# Martinkatu footbridge

The generic draft bridge, stairs and retaining-edge generators now reconstruct
Betanianpolku over Martinkatu from its mapped crossing and access ways. No
bridge-specific model or generator coordinates were added.

The OSM bridge area includes part of an access flight. The generator uses the
crossing way for a stair-access bridge's main axis and clips the deck outline to
that crossing. This makes the lidar deck survey valid instead of falling back to
the road's ground heights. Short connecting footways form suspended landings;
stairs own their climbs, and the bridge does not raise terrain beneath them.

Optional cached classified lidar supplies profiles for bridge-connected stairs,
including stairs attached to an interior node of a connecting footway. Dominant
surface returns, monotonic fitting and measured plateaus preserve the flights and
landings. Coverage must reach 75%, both endpoints must be measured, and no gap
between supported stations may exceed four metres. The final three metres absorb
the small join difference between a mapped stair endpoint and the deck survey.
Ordinary stairs continue to use the existing terrain fallback.

Road footbridges receive 0.52 m diameter concrete columns outside the mapped
carriageways instead of thick end walls or transverse pier blocks. Stair-access
concrete bridges use a 0.32 m deck and white tubular railing. Their suspended
stairs have thin soffits, while terrain-supported sides retain their foundation.
The smooth stair collision includes the landing aprons and meets deck height.
Mapped retaining walls and natural cliff lines replace the coarse cross-slope
with a face below grade and matching terrain strips; no wall is invented on level
terrain. These changes preserve the existing building facade and texture assets.

## Local component evidence

The supplied three private reference images were inspected locally. No reference
imagery or raw city data was added to the public tree.

The component audit measured the crossing at 27.432 m, with the deck at N2000
15.045–15.775 m. Its held-out lidar p90 residual is 0.035 m before the existing
2 cm surface offset. Two columns are outside the carriageway envelopes. Minimum
sampled vehicle clearance is 5.697 m, exceeding the 4.5 m requirement.

All three mapped access stairs have lidar profiles. The turning flight
51850389 has 100% supported stations, the long straight flight 51850391 has 80%,
and the short upper flight 152098490 has 100%. Their maximum walking-collider
grades are 41.39°, 43.71° and 32.96°. The two long access ways retain their level
landings. The cached local bridge/stair/retaining component pass took 4.8 seconds;
it is not a whole-tile build timing.

Other named footbridges in the same source clip receiving tubular railing
geometry include Teatterisilta, Kirjastosilta, Humalistonsilta and Logomonsilta.
Existing material choices and deck thickness remain on bridges without stair
access. Road and railway bridges keep their existing railing construction.

## Verification

Run from an assigned sandbox after sourcing its environment:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_footbridge.py pipeline/tests/test_coarse_bridges.py
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_surfaces.py -k stair
python3 scripts/dev/probe_footbridge.py --commit "$(git rev-parse HEAD)"
```

The deployed probe builds real first and full passes for `t0_-2_3` and the
Teatterisilta control tile `t0_0_1`, compares building/collision counts with source,
and audits Martinkatu's deployed-source deck survey, clearance, columns, measured
stair landings, walkable grades and retaining output. The agent-owned async job
has the repository's hard ten-minute limit, including any slot wait.
