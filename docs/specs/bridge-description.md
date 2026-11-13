# Bridge description: `turku-description/bridge/1`

The structure of a bridge in the world pipeline: abutments, piers, the girders or arches between them, edge beams,
parapets, end pylons, arched openings in the abutments and grilles where a passage under the deck is gated. The
deck's walking surface and its slab stay the ground kind's (`kinds/ground/decks.py`, `mesh.py`), so streets, kerbs,
paint and paving run on across the bridge. Code: `pipeline/turku3d/world/kinds/bridge/` (`survey.py` facts,
`build.py` geometry, `profiles.json` what the map does not say about a named bridge).

## Identity

One entity per deck that `decks.spans` finds: a way with a `bridge` tag over mapped water, its outline from the street
register's `Silta` polygons or a strip as wide as its class. `id` is `bridge/osm-way-<id of the axis way>`; the box
is the deck outline's bounds. Bridges over land are not decks yet and have no entity.

## Facts (from the sources, measured along the axis)

| field | meaning |
|---|---|
| `axis` | `[[E, N], ...]` the deck's axis from abutment to abutment (EPSG:3877); stations are metres along it |
| `rings` | deck outline rings `[[E, N], ...]` |
| `sides` | the outline's two long side chains, left of the axis first |
| `ends` | carriageway level (N2000) at the axis's start and end; linear between them |
| `name`, `wikidata`, `year` | OSM tags of the outline (`man_made=bridge`) over the way's; `year` from `start_date` |
| `structure`, `material` | OSM `bridge:structure`, `bridge:material` (empty when unmapped) |
| `use` | `road`, `foot` or `rail`: what the ways on the deck carry |
| `water` | `[[s0, s1], ...]` stations over mapped water |
| `faces` | `[s0, s1]` stations of the abutment faces: where the bank beside the deck rises `FACE_RISE` above its foot |
| `banks` | levels of the banks at the two faces |
| `ground` | `{step, heights}`: the lower ground just outside the deck's sides, every `step` m |
| `piers.measured` | stations of piers the lidar shows over the water (vertical clusters of returns under the deck) |
| `gates` | `[{station, key, locked}]` mapped `barrier=gate` nodes in passages under the deck |

## Profile (`profile`, data)

`profiles.json` (`turku-bridge-profiles/1`) holds per Wikidata id what the map does not say; a stored description's
`profile` keys win over it. Every key is optional and overrides a type rule:

| key | meaning |
|---|---|
| `body`, `support` | material of the spans and of the abutments and piers: `stone`, `concrete`, `steel`, `wood` |
| `structure` | `beam`, `arch`, `humpback`, `cable-stayed`, `suspension` |
| `parapets` | `{approach, span}`: `solid` (granite), `balustrade` (plinth, balusters, dies, coping), `railing` (vertical bars) or `lattice` (heritage diamond panels) |
| `rail_colour`, `body_colour` | `#rrggbb` |
| `piers` | `{fractions: [...], thickness}`: piers at fractions of the face-to-face span, snapped to measured ones |
| `pylons` | `{height, width, lantern}`: granite obelisks with a bronze collar at both sides of both faces |
| `arches` | `[{end, facing, from_face, width, height, grille}]`: an arched opening in abutment `end` (0, 1), its centre `from_face` m behind the face, in the side face turned to the compass bearing `facing` (both when absent) |
| `slab` | deck depth for thin decks |
| `evidence` | where the values come from (photos, the old generator) |

## Type rules (`build.style`)

- Body: `bridge:material`, else steel for a foot bridge, concrete otherwise. Support: stone before 1940 or for a stone
  body, else concrete.
- Parapets: `solid` over the abutments of a road bridge before 1940, `solid` over the spans of a stone bridge,
  `railing` elsewhere; 1.1 m on road bridges, 1.0 m on foot bridges. A parapet stands only where the ground beside
  the deck lies more than 0.6 m under it.
- Piers: the measured ones, plus as many as the type's longest span asks for between the supports (beam 40 m, foot
  35 m, arch 60 m, cable-stayed 80 m, suspension 120 m).
- Girders: haunched, deepest over the supports (span/14 to span/30 concrete, span/16 to span/35 steel), arch soffits
  for `arch` and `humpback`, never lower than 0.8 m above the water or 2.4 m above a bank under the deck.

## Collision

The deck is walked and driven on through the ground's height raster. The bridge's drawn mesh (abutments, parapet
solids, girders, pylons) is solid (`world/collision.py: MESH_KINDS`); railing panels and posts are fixed box
primitives.
