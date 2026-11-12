# Prepared region selection

Normal gameplay reads `/scenes/regions.json` using the single
`turku-prepared-regions/1` contract. Without `?region=`, it loads the catalog's
default region. `?region=port-arthur` selects that exact catalog entry. An empty
or unknown request is an error; it never silently loads another area.

Each descriptor supplies `id`, `label`, a same-origin
`/scenes/[slug].scene.json` URL, complete release hash, bounds and player/car
spawn. The client checks the selected scene's identity, release, bounds and both
spawn poses before allocating scene resources or publishing collision. The
existing strict prepared scene reader handles every region. There is no legacy
world fallback or alternative rendering path.

Solo saves already use the scene ID. Non-default regions also scope multiplayer
session/reset tokens and mission snapshots by region, so a retained car or pose
from the office cannot become the initial state in another bounded scene. The
default region retains its established keys. The Turku multiplayer room and
coordinate frame remain shared.

Publish the catalog and all referenced scene documents together. Every descriptor
must be refreshed when its scene release changes, and deployment must include the
asset and collision closure of every catalog entry. The runtime rejects stale
descriptors. The catalog is scene content, not a renderer feature flag.
