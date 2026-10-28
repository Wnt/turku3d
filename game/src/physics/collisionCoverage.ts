import { COLLISION_TILE, collisionTileId } from '../core/origin';

/** Every cell touched by an actor's swept ground footprint must have a collider in this worker.
 * Manifest membership and decoded draft buffers are insufficient. Includes stationary actors and negative cells. */
export function collisionCovers(loaded: { has(id: string): boolean }, x: number, z: number,
  nextX = x, nextZ = z, radius = 0): boolean {
  const i0 = Math.floor((Math.min(x, nextX) - radius) / COLLISION_TILE);
  const i1 = Math.floor((Math.max(x, nextX) + radius) / COLLISION_TILE);
  const j0 = Math.floor((Math.min(z, nextZ) - radius) / COLLISION_TILE);
  const j1 = Math.floor((Math.max(z, nextZ) + radius) / COLLISION_TILE);
  for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) {
    if (!loaded.has(collisionTileId(i, j))) return false;
  }
  return true;
}
