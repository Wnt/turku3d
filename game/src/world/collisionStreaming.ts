// Streams .col tiles within `radius` metres of the focus point into the physics worker; unloads beyond radius + hysteresis.
import { distToCollisionTile, parseCollisionId } from '../core/origin';
import type { Manifest } from './manifest';
import type { PhysicsClient } from '../physics/client';

export class CollisionStreamer {
  private loading = new Set<string>();
  private failed = new Set<string>();
  private lastCheck = -Infinity;
  readonly maxConcurrent = 4;
  constructor(private manifest: Manifest, private physics: PhysicsClient, public radius = 200, public hysteresis = 50) {}

  /** Call every frame; checks at most 4x per second unless forced. Returns ids requested this call. */
  update(x: number, z: number, force = false): string[] {
    const now = performance.now();
    if (!force && now - this.lastCheck < 250) return [];
    this.lastCheck = now;
    const want: { id: string; d: number }[] = [];
    for (const id of Object.keys(this.manifest.collision)) {
      const ij = parseCollisionId(id);
      if (!ij) continue;
      const d = distToCollisionTile(x, z, ij.i, ij.j);
      if (this.physics.loaded.has(id)) { if (d > this.radius + this.hysteresis) this.physics.unloadTile(id); }
      else if (d <= this.radius && !this.loading.has(id) && !this.failed.has(id)) want.push({ id, d });
    }
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
    return Object.keys(this.manifest.collision).filter((id) => {
      const ij = parseCollisionId(id)!;
      return distToCollisionTile(x, z, ij.i, ij.j) <= r && !this.physics.loaded.has(id) && !this.failed.has(id);
    });
  }
}
