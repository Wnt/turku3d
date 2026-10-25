# game/src/shared/col — `.col` reader + Rapier builder (TypeScript)

Shared by the game physics worker and `tools/validator`. Implements
[docs/specs/col-format.md](../../../../docs/specs/col-format.md) v1.0. Erasable TypeScript only (Node 24 runs it
directly), no package imports: the meshopt decoder and the Rapier module are passed in.

```ts
import { MeshoptDecoder } from 'meshoptimizer';
import RAPIER from '@dimforge/rapier3d';            // or '@dimforge/rapier3d-compat'
import { readCol, buildTileColliders, CHARACTER_FILTER } from '../shared/col/index.ts';

await MeshoptDecoder.ready;
const tile = readCol(arrayBuffer, { decoder: MeshoptDecoder, verifyCrc: true });
const built = buildTileColliders(RAPIER, world, tile);  // one trimesh per class + primitives, translation = origin
cc.computeColliderMovement(collider, move, RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, CHARACTER_FILTER);
```

| File | What |
|---|---|
| `colFormat.ts` | `readCol`, `readColHeader`, `crc32`, `splitByClass`, `checkShippable`, types |
| `rapierBuild.ts` | `buildTileColliders`, interaction groups (`STATIC_GROUPS`, `CHARACTER_FILTER`, `SOLID_QUERY_FILTER`, `QUERY_HELPERS_FILTER`), `hitMaterial` |
| `materials.ts` | **generated** from materials.md (`make col-materials`); a pytest fails when stale |
| `index.ts` | barrel |

Notes
- Classes: `solid` trimesh (STATIC_SOLID), `query` trimesh (STATIC_QUERY, e.g. stair_tread_helper), `sensor`
  trimesh (`setSensor(true)`, water). The KCC must use `CHARACTER_FILTER` so query helpers never block it.
- `FIX_INTERNAL_EDGES` header bit → `RAPIER.TriMeshFlags.FIX_INTERNAL_EDGES` (144 in 0.21 = includes vertex merging).
- `hitMaterial(built, handle, featureId)` maps a ray/shape hit back to the material id (trimesh feature id = triangle index
  of that class sub-mesh). If Rapier ever drops triangles while building (it does not with these flags) this mapping would shift.
- Tests: `make col-test-ts` (tools/validator/test/roundtrip.test.ts) and `make col-typecheck`. The files also
  typecheck cleanly under `game/tsconfig.json`.
