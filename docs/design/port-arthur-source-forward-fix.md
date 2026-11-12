# Port Arthur source repair

The first native held-out capture exposed road peaks that the central route
checks did not cover. At four sampled peaks, source class-12 overlap returns
raised the prepared road to 9.320–9.438 m. Nearby class-2 ground was approximately
8.88–8.94 m. Overlap is a flight-strip classification, not a ground label: the
previous distance-scaled height allowance admitted parked-car skirts.

`scene.ground_survey.ground_returns` now preserves every class-2 return and
accepts class-12 returns only within 8 cm of a sufficiently supported local
class-2 plane, or within 8 cm of a classified return less than 30 cm away.
Unstable, rank-deficient and stepped neighbourhoods cannot validate a plane.
The original classified elevations and genuine separated ground levels are not
smoothed. Only the Port Arthur survey and measured street sections were
refrozen; the accepted riverfront survey stays pinned.

The repaired four positions are (-957.992,307.217), (-958.415,308.336),
(-962.650,310.629) and (-946.525,300.476), in engine XZ. Their canonical heights
are now 8.923, 8.920, 8.919 and 8.937 m. The original two street routes and an
additional lateral route through the visible defect pass production Rapier
capsule traversal in both directions. Native paired capture remains necessary
for visual acceptance.

The forward recipe also restores cached surrounding output. The
`scene/retain_buildings.mjs` adapter selects explicit building-ID index ranges,
excludes the prepared entities and retains every source appearance attribute,
material and original binary byte. A bounded range selects neighboring context;
whole selected building entities are retained. Source tree identities, transforms,
parents and crowns are reused. These visual context assets do not expand the
playable collision boundary. The 14 qualified held-out appearance records stay
unchanged.

`scene.road_markings` reads retained explicit mapped paint polygons and clips
them to canonical road supports. New paint meshes are passable, use a documented
2 mm optical lift and interpolate the receiver's baked visibility and lighting;
they add no collision surfaces. Ambiguous shared sign mounts remain withheld.
The player and car spawn points are separated by approximately 12.2 m on the
supported street rather than 2 m.

Raw lidar, source caches and reference captures stay private. The recipe pins
the survey and marking input hashes; prepared context assets use the canonical
content-addressed store and retain original source hashes in scene provenance.
