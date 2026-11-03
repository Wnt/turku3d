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
import { GroundLampField } from './groundField';
import { FloodMasts, type FloodTile } from './floodlights';
import { FloodArea, areaVenues } from './floodArea';

/** Main-thread budget per frame for placing newly streamed tiles' lamps, ms. */
const STEP_MS = 1.5;
const SYNC_MS = 250;

/** Drawn draft tiles with their final visual model (collision previews are not the visual surface). */
function* drawnTiles(entries: ReadonlyMap<string, Entry>): Generator<LampSource> {
  for (const [id, e] of entries) if (e.state === 'draft' && e.model && !e.preview) yield { id, model: e.model, lights: e.lights ?? null };
}

/** A previewed post: new base (world metres) and yaw (rad, three.js rotation.y). */
export interface LampEdit { x: number; y: number; z: number; yaw: number }

/** The lamp moved to a new base and yaw; its light head follows rigidly. The slot key stays, so the post keeps its slot. */
export function moveLamp(lamp: Lamp, edit: LampEdit): Lamp {
  const turn = edit.yaw - lamp.yaw, c = Math.cos(turn), s = Math.sin(turn);
  const dx = lamp.light.x - lamp.x, dz = lamp.light.z - lamp.z;
  // three.js rotation.y: x' = x cos + z sin, z' = -x sin + z cos.
  const light = { ...lamp.light, x: edit.x + dx * c + dz * s, y: lamp.light.y + edit.y - lamp.y, z: edit.z - dx * s + dz * c };
  return { ...lamp, x: edit.x, y: edit.y, z: edit.z, yaw: edit.yaw, light };
}

export class StreetLights {
  readonly sprites: LightSprites;
  readonly posts = new LampPosts();
  readonly field = new LampField();
  /** Real light pools of the nearest lamps in the fixed point slots (none on mobile and low). */
  readonly pools: LampPools;
  readonly ground: GroundLampField;
  /** Poles of the floodlight masts the city does not draw already. */
  readonly masts: FloodMasts;
  /** Floodlit venues' light on everything around them (the light set's area light). */
  readonly area = new FloodArea();
  night = 0;
  private floodMasts = 0;
  private floodVenues = 0;
  private tiles = new Map<string, readonly Lamp[]>();
  /** The tiles' own lamps before developer editor previews (dev/editor), and those previews by lamp key. */
  private raw = new Map<string, readonly Lamp[]>();
  private edits = new Map<string, LampEdit | null>();
  private footprints = new Map<string, Footprint>();
  private flood = new Map<string, FloodTile>();
  private source: { entries: ReadonlyMap<string, Entry> } | null = null;
  private nextSync = 0;
  private postsDirty = false;
  private size = new THREE.Vector2();
  private forward = new THREE.Vector3();

  /** `override` (?lights=) fixes the night level. `slots` are Environment.lamps.point. */
  constructor(scene: THREE.Scene, tier: QualityTier, private override: number | null = null, slots: readonly THREE.PointLight[] = []) {
    this.sprites = new LightSprites(tier);
    this.pools = new LampPools(slots, footprintSight(() => this.footprints.values()));
    this.ground = new GroundLampField(() => this.footprints.values());
    // The masts share the posts' material (and its prebuilt pipeline): no new shader at night.
    this.masts = new FloodMasts(this.posts.mesh.material as THREE.Material);
    scene.add(this.posts.mesh, this.masts.mesh);
    this.field.onTile = (id, lamps, footprint, flood) => {
      if (lamps) this.raw.set(id, lamps); else this.raw.delete(id);
      const drawn = lamps ? this.edited(lamps) : null;
      if (drawn) this.tiles.set(id, drawn); else this.tiles.delete(id);
      if (footprint) this.footprints.set(id, footprint); else this.footprints.delete(id);
      if (drawn && flood) this.flood.set(id, flood); else this.flood.delete(id);
      this.sprites.setGroup(`tile:${id}`, drawn?.map(l => l.kind === 'street' ? { ...l.light, headRadius: 0.1 * l.scale } : l.light) ?? null);
      this.postsDirty = true;
    };
  }

  /** Street-light posts of the drawn tiles with their tile (dev/editor picking): the tiles' own, unedited posts. */
  forEachPost(visit: (tile: string, lamp: Lamp, index: number) => void) {
    for (const [tile, lamps] of this.raw) lamps.forEach((lamp, i) => { if (lamp.post) visit(tile, lamp, i); });
  }

  /** The tile's own (unedited) lamp with this key. */
  originalLamp(key: string): { tile: string; lamp: Lamp; index: number } | null {
    for (const [tile, lamps] of this.raw) { const index = lamps.findIndex(l => l.key === key); if (index >= 0) return { tile, lamp: lamps[index], index }; }
    return null;
  }

  /** Preview an edited post (visual only): its new base and yaw, null removes the lamp, undefined restores it. */
  setEdit(key: string, edit: LampEdit | null | undefined) {
    if (edit === undefined) this.edits.delete(key); else this.edits.set(key, edit);
    for (const [tile, lamps] of this.raw) {
      if (!lamps.some(l => l.key === key)) continue;
      const edited = this.edited(lamps);
      this.tiles.set(tile, edited);
      this.sprites.setGroup(`tile:${tile}`, edited.map(l => l.kind === 'street' ? { ...l.light, headRadius: 0.1 * l.scale } : l.light));
      this.postsDirty = true;
    }
  }

  private edited(lamps: readonly Lamp[]): readonly Lamp[] {
    if (!this.edits.size) return lamps;
    const out: Lamp[] = [];
    for (const lamp of lamps) {
      const edit = this.edits.get(lamp.key);
      if (edit === null) continue;
      out.push(edit === undefined ? lamp : moveLamp(lamp, edit));
    }
    return out;
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
      this.ground.setLamps(all);
      const flood = [...this.flood.values()];
      const masts = flood.flatMap(f => f.masts);
      this.area.setVenues(areaVenues(flood.flatMap(f => f.venues), masts));
      this.floodMasts = this.masts.setMasts(masts);
      this.floodVenues = flood.reduce((n, f) => n + f.venues.length, 0);
      if (this.pools.size) this.pools.setLamps(all.map(poolLamp).filter((l): l is PoolLamp => l !== null));
    }
    this.night = this.override ?? nightLevel(inputs);
    this.posts.night.value = this.night;
    this.ground.update(camera.position.x, camera.position.z, this.night);
    this.area.update(camera.position.x, camera.position.z, this.night);
    this.posts.update(camera.position.x, camera.position.z, now);
    camera.getWorldDirection(this.forward);
    this.pools.update(camera.position.x, camera.position.z, this.forward.x, this.forward.z, this.night, now);
    renderer.getDrawingBufferSize(this.size);
    this.sprites.update(camera, this.size, this.night);
  }

  snapshot() {
    let lamps = 0, posts = 0;
    for (const list of this.tiles.values()) { lamps += list.length; posts += list.filter(l => l.post).length; }
    return { tiles: this.tiles.size, pending: this.field.pending() || this.ground.pending, lamps, posts, postsDrawn: this.posts.drawn,
      sprites: this.sprites.count, spriteCapacity: this.sprites.capacity, spritesDropped: this.sprites.dropped, night: this.night,
      pools: this.pools.snapshot(), floodVenues: this.floodVenues, floodMasts: this.floodMasts, floodLit: this.area.snapshot(),
      floodHeads: [...this.tiles.values()].reduce((n, list) => n + list.filter(l => l.kind === 'flood').length, 0) };
  }

  dispose() {
    this.posts.mesh.removeFromParent(); this.masts.mesh.removeFromParent();
    this.masts.dispose(); this.posts.dispose(); this.sprites.dispose(); this.ground.dispose(); this.area.dispose();
  }
}
