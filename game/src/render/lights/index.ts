// Street lights for main.ts: lamps follow the resident draft tiles, posts are drawn near the camera and every
// light's corona goes into the one global sprite draw composited after TRAA (render/post.ts).
import * as THREE from 'three/webgpu';
import type { Entry } from '../../world/coarseContracts';
import type { QualityTier } from '../quality';
import { LampField, type Lamp, type LampSource } from './lampField';
import { LampPosts } from './lampPosts';
import { LightSprites } from './sprites';
import { LampPools, footprintSight, poolLamp, type PoolLamp } from './pools';
import type { Footprint } from './tileScan';
import { nightLevel, type NightInputs } from './night';

/** Main-thread budget per frame for placing newly streamed tiles' lamps, ms. */
const STEP_MS = 1.5;
const SYNC_MS = 250;

/** Drawn draft tiles with their final visual model (collision previews are not the visual surface). */
function* drawnTiles(entries: ReadonlyMap<string, Entry>): Generator<LampSource> {
  for (const [id, e] of entries) if (e.state === 'draft' && e.model && !e.preview) yield { id, model: e.model, lights: e.lights ?? null };
}

export class StreetLights {
  readonly sprites: LightSprites;
  readonly posts = new LampPosts();
  readonly field = new LampField();
  /** Real light pools of the nearest lamps in the fixed point slots (none on mobile and low). */
  readonly pools: LampPools;
  night = 0;
  private tiles = new Map<string, readonly Lamp[]>();
  private footprints = new Map<string, Footprint>();
  private source: { entries: ReadonlyMap<string, Entry> } | null = null;
  private nextSync = 0;
  private postsDirty = false;
  private size = new THREE.Vector2();
  private forward = new THREE.Vector3();

  /** `override` (?lights=) fixes the night level. `slots` are Environment.lamps.point. */
  constructor(scene: THREE.Scene, tier: QualityTier, private override: number | null = null, slots: readonly THREE.PointLight[] = []) {
    this.sprites = new LightSprites(tier);
    this.pools = new LampPools(slots, footprintSight(() => this.footprints.values()));
    scene.add(this.posts.mesh);
    this.field.onTile = (id, lamps, footprint) => {
      if (lamps) this.tiles.set(id, lamps); else this.tiles.delete(id);
      if (footprint) this.footprints.set(id, footprint); else this.footprints.delete(id);
      this.sprites.setGroup(`tile:${id}`, lamps?.map(l => l.light) ?? null);
      this.postsDirty = true;
    };
  }

  /** Follow a draft tile source (CoarseTiles). */
  attach(source: { entries: ReadonlyMap<string, Entry> }) { this.source = source; this.nextSync = 0; }

  /** Per frame, before the post pipeline renders. */
  update(camera: THREE.PerspectiveCamera, renderer: THREE.WebGPURenderer, inputs: NightInputs, now = performance.now()) {
    if (this.source && now >= this.nextSync) { this.nextSync = now + SYNC_MS; this.field.sync(drawnTiles(this.source.entries)); }
    this.field.step(STEP_MS);
    if (this.postsDirty) {
      this.postsDirty = false;
      const all = [...this.tiles.values()].flat();
      this.posts.setLamps(all.filter(l => l.post));
      if (this.pools.size) this.pools.setLamps(all.map(poolLamp).filter((l): l is PoolLamp => l !== null));
    }
    this.night = this.override ?? nightLevel(inputs);
    this.posts.night.value = this.night;
    this.posts.update(camera.position.x, camera.position.z, now);
    camera.getWorldDirection(this.forward);
    this.pools.update(camera.position.x, camera.position.z, this.forward.x, this.forward.z, this.night, now);
    renderer.getDrawingBufferSize(this.size);
    this.sprites.update(camera, this.size, this.night);
  }

  snapshot() {
    let lamps = 0, posts = 0;
    for (const list of this.tiles.values()) { lamps += list.length; posts += list.filter(l => l.post).length; }
    return { tiles: this.tiles.size, pending: this.field.pending(), lamps, posts, postsDrawn: this.posts.drawn,
      sprites: this.sprites.count, spriteCapacity: this.sprites.capacity, spritesDropped: this.sprites.dropped, night: this.night,
      pools: this.pools.snapshot() };
  }

  dispose() { this.posts.mesh.removeFromParent(); this.posts.dispose(); this.sprites.dispose(); }
}
