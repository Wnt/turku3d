// Build Rapier colliders from a decoded .col tile (spec §7). Works with both
// `@dimforge/rapier3d` (game worker) and `@dimforge/rapier3d-compat` (Node validator): the Rapier
// module is passed in, so this file has no package import.

import { GROUP, MATERIALS, materialClass } from './materials.ts';
import type { CollisionClass } from './materials.ts';
import { COL_FLAG, PRIM_KIND, splitByClass } from './colFormat.ts';
import type { ColTile } from './colFormat.ts';

/** Rapier interaction groups: (membership << 16) | filter. */
export function groups(membership: number, filter: number): number {
  return ((membership & 0xffff) << 16) | (filter & 0xffff);
}

export const STATIC_GROUPS: Record<CollisionClass, number> = {
  solid: groups(GROUP.STATIC_SOLID, 0xffff),
  query: groups(GROUP.STATIC_QUERY, 0xffff),
  sensor: groups(GROUP.STATIC_SENSOR, 0xffff),
};
/** Use as `filterGroups` for the character controller: collides with static solids only. */
export const CHARACTER_FILTER = groups(GROUP.CHARACTER, GROUP.STATIC_SOLID);
/** Scene ray queries that should only hit solids. */
export const SOLID_QUERY_FILTER = groups(0xffff, GROUP.STATIC_SOLID);
/** Foot-IK rays: solids plus query helpers (stair treads). */
export const QUERY_HELPERS_FILTER = groups(0xffff, GROUP.STATIC_SOLID | GROUP.STATIC_QUERY);

export interface BuiltCollider {
  collider: any; // RAPIER.Collider
  cls: CollisionClass;
  kind: 'trimesh' | 'box' | 'capsule' | 'cylinder';
  /** trimesh: material per triangle (Rapier feature id = triangle index). primitive: one material. */
  materials: Uint8Array;
  /** trimesh: tile triangle index of each collider triangle. */
  triangleIds?: Uint32Array;
}

export interface BuiltTile {
  tileId: string;
  colliders: BuiltCollider[];
  buildMs: number;
  /** Map from collider handle to BuiltCollider, for material lookup after queries. */
  byHandle: Map<number, BuiltCollider>;
}

/**
 * Create static colliders for a tile in `world`. Each class present gets one trimesh collider;
 * primitives become separate colliders. Collider translation = tile origin (float32 vertices stay
 * tile-local). Sensors are flagged as sensors. Trimeshes use FIX_INTERNAL_EDGES when the header says so.
 */
export function buildTileColliders(RAPIER: any, world: any, tile: ColTile): BuiltTile {
  const t0 = performance.now();
  const [ox, oy, oz] = tile.header.origin;
  const fix = (tile.header.flags & COL_FLAG.FIX_INTERNAL_EDGES) !== 0;
  const out: BuiltCollider[] = [];
  for (const m of splitByClass(tile)) {
    const desc = fix
      ? RAPIER.ColliderDesc.trimesh(m.positions, m.indices, RAPIER.TriMeshFlags.FIX_INTERNAL_EDGES)
      : RAPIER.ColliderDesc.trimesh(m.positions, m.indices);
    desc.setTranslation(ox, oy, oz).setCollisionGroups(STATIC_GROUPS[m.cls]).setFriction(averageFriction(m.materials));
    if (m.cls === 'sensor') desc.setSensor(true);
    out.push({ collider: world.createCollider(desc), cls: m.cls, kind: 'trimesh', materials: m.materials, triangleIds: m.triangleIds });
  }
  for (const p of tile.primitives) {
    const cls = materialClass(p.material);
    let desc: any;
    let kind: BuiltCollider['kind'];
    if (p.kind === PRIM_KIND.BOX) {
      desc = RAPIER.ColliderDesc.cuboid(p.params[0], p.params[1], p.params[2]);
      kind = 'box';
    } else if (p.kind === PRIM_KIND.CAPSULE) {
      desc = RAPIER.ColliderDesc.capsule(p.params[0], p.params[1]);
      kind = 'capsule';
    } else {
      desc = RAPIER.ColliderDesc.cylinder(p.params[0], p.params[1]);
      kind = 'cylinder';
    }
    const [qx, qy, qz, qw] = p.rotation;
    desc
      .setTranslation(ox + p.center[0], oy + p.center[1], oz + p.center[2])
      .setRotation({ x: qx, y: qy, z: qz, w: qw })
      .setCollisionGroups(STATIC_GROUPS[cls])
      .setFriction(MATERIALS[p.material]?.friction ?? 0.8);
    if (cls === 'sensor') desc.setSensor(true);
    out.push({ collider: world.createCollider(desc), cls, kind, materials: Uint8Array.of(p.material) });
  }
  const byHandle = new Map<number, BuiltCollider>();
  for (const c of out) byHandle.set(c.collider.handle, c);
  return { tileId: tile.id, colliders: out, buildMs: performance.now() - t0, byHandle };
}

function averageFriction(mats: Uint8Array): number {
  // One friction per trimesh collider (Rapier has no per-triangle friction); per-material friction
  // is applied by gameplay code through the triangle material lookup.
  const counts = new Map<number, number>();
  for (const m of mats) counts.set(m, (counts.get(m) ?? 0) + 1);
  let best = 0, bestN = -1;
  for (const [m, n] of counts) if (n > bestN) { best = m; bestN = n; }
  return MATERIALS[best]?.friction ?? 0.8;
}

/** Material id of a ray/shape hit (`featureId` = triangle index for trimeshes). */
export function hitMaterial(tile: BuiltTile, colliderHandle: number, featureId: number | undefined): number | undefined {
  const c = tile.byHandle.get(colliderHandle);
  if (!c) return undefined;
  if (c.kind !== 'trimesh') return c.materials[0];
  return featureId === undefined ? undefined : c.materials[featureId];
}
