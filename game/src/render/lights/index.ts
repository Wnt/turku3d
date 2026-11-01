// Street lights for main.ts: lamps follow the resident draft tiles, posts are drawn near the camera and every
// light's corona goes into the one global sprite draw composited after TRAA (render/post.ts).
import * as THREE from 'three/webgpu';
import type { Entry } from '../../world/coarseContracts';
import type { QualityTier } from '../quality';
import { LampField, type Lamp, type LampSource } from './lampField';
import { LampPosts } from './lampPosts';
import { LightSprites } from './sprites';
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
  night = 0;
  private tiles = new Map<string, readonly Lamp[]>();
  private source: { entries: ReadonlyMap<string, Entry> } | null = null;
  private nextSync = 0;
  private postsDirty = false;
  private size = new THREE.Vector2();

  /** `override` (?lights=) fixes the night level, e.g. 1 to see the lights in today's daylight-only build. */
  constructor(scene: THREE.Scene, tier: QualityTier, private override: number | null = null) {
    this.sprites = new LightSprites(tier);
    scene.add(this.posts.mesh);
    this.field.onTile = (id, lamps) => {
      if (lamps) this.tiles.set(id, lamps); else this.tiles.delete(id);
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
      this.posts.setLamps([...this.tiles.values()].flatMap(lamps => lamps.filter(l => l.post)));
    }
    this.night = this.override ?? nightLevel(inputs);
    this.posts.night.value = this.night;
    this.posts.update(camera.position.x, camera.position.z, now);
    renderer.getDrawingBufferSize(this.size);
    this.sprites.update(camera, this.size, this.night);
  }

  snapshot() {
    let lamps = 0, posts = 0;
    for (const list of this.tiles.values()) { lamps += list.length; posts += list.filter(l => l.post).length; }
    return { tiles: this.tiles.size, pending: this.field.pending(), lamps, posts, postsDrawn: this.posts.drawn,
      sprites: this.sprites.count, spriteCapacity: this.sprites.capacity, spritesDropped: this.sprites.dropped, night: this.night };
  }

  dispose() { this.posts.mesh.removeFromParent(); this.posts.dispose(); this.sprites.dispose(); }
}
