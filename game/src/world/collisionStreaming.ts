// Streams .col tiles within `radius` metres of the focus point into the physics worker; unloads beyond radius + hysteresis.
import { COLLISION_TILE, collisionTileId, distToCollisionTile, parseCollisionId } from '../core/origin';
import type { Manifest } from './manifest';
import type { PhysicsClient } from '../physics/client';

export class CollisionStreamer {
  private loading = new Set<string>();
  private failed = new Set<string>();
  private lastCheck = -Infinity;
  readonly maxConcurrent = 4;
  /** Tile ids within `r` of (x,z), found through the i/j grid window instead of a scan over the whole manifest
   *  (a city-wide manifest has ~80k collision tiles: a full scan with id parsing costs ~20 ms per call). */
  private near(x: number, z: number, r: number): { id: string; d: number }[] {
    const out: { id: string; d: number }[] = [];
    const i0 = Math.floor((x - r) / COLLISION_TILE), i1 = Math.floor((x + r) / COLLISION_TILE);
    const j0 = Math.floor((z - r) / COLLISION_TILE), j1 = Math.floor((z + r) / COLLISION_TILE);
    for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) {
      const id = collisionTileId(i, j);
      if (!this.manifest.collision[id]) continue;
      const d = distToCollisionTile(x, z, i, j);
      if (d <= r) out.push({ id, d });
    }
    return out;
  }
  constructor(private manifest: Manifest, private physics: PhysicsClient, public radius = 200, public hysteresis = 50) {}

  /** Call every frame; checks at most 4x per second unless forced. Returns ids requested this call. */
  update(x: number, z: number, force = false): string[] {
    const now = performance.now();
    if (!force && now - this.lastCheck < 250) return [];
    this.lastCheck = now;
    for (const id of [...this.physics.loaded.keys()]) {
      const ij = parseCollisionId(id);
      if (ij && distToCollisionTile(x, z, ij.i, ij.j) > this.radius + this.hysteresis) this.physics.unloadTile(id);
    }
    // Keep unloading while requests are in flight, but only search/sort when a slot is available.
    if (this.loading.size >= this.maxConcurrent) return [];
    const want = this.near(x, z, this.radius).filter(({ id }) => !this.physics.loaded.has(id) && !this.loading.has(id) && !this.failed.has(id));
    want.sort((a, b) => a.d - b.d);
    const started: string[] = [];
    for (const { id } of want) {
      if (this.loading.size >= this.maxConcurrent) break;
      this.loading.add(id); started.push(id);
      void this.fetchAndLoad(id);
    }
    return started;
  }

  private async fetchAndLoad(id: string) {
    const e = this.manifest.collision[id];
    try {
      const res = await fetch(new URL(e.url, this.manifest.baseUrl));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      if (buf.byteLength !== e.bytes) throw new Error(`size ${buf.byteLength} != manifest ${e.bytes}`);
      await this.physics.loadTile(id, buf);
    } catch (err) {
      this.failed.add(id);
      console.error(`[collision] ${id}: ${(err as Error).message}`);
    } finally {
      this.loading.delete(id);
    }
  }
  get pending(): number { return this.loading.size; }
  /** Ids within radius that are not loaded yet (ignores failed ones). */
  missingNear(x: number, z: number, r: number): string[] {
    return this.near(x, z, r).map((t) => t.id).filter((id) => !this.physics.loaded.has(id) && !this.failed.has(id));
  }
}
