// Coarse (draft) tiles on localhost: when the player comes within one tile of an L0 cell the published tileset lacks,
// the companion service (tools/dev/coarse-tiles, proxied at /coarse/) builds it in seconds and this module streams it
// in like a published tile: the GLB goes into the scene, its .col entries into the manifest so CollisionStreamer
// loads them with the normal residency rules. Collision comes first: a draft is drawn only when all 16 of its cells
// have collision and the physics worker has decoded each of its .col files; one that fails is never drawn (the
// physics edge walls keep the player out of cells without loaded collision). Draft tiles get a desaturated tint and a HUD line. Published 500 m
// leaf tiles are hidden once all four of their quadrants are drafted (the same REPLACE rule as the tileset).
// Every 60 s the published manifest is re-read; a tile that has been published there replaces its draft.
// Never active off localhost (GitHub Pages) or when the service is not running.
import * as THREE from 'three/webgpu';
import { materialColor, saturation, vec3 } from 'three/tsl';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { COLLISION_TILE, VISUAL_L0 } from '../core/origin';
import type { Manifest, ManifestEntry } from './manifest';

const COARSE_ROOT = '/coarse/';
const POLL_MS = 1000;
const MANIFEST_POLL_MS = 60_000;
/** A failed or rejected draft is asked for again after this (ms); the service retries failed builds after 120 s. */
const RETRY_MS = 120_000;
/** Drafts farther than this (m, to the tile footprint) are unloaded. */
const KEEP_M = 2 * VISUAL_L0;

export function isLocalHost(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
}

const l0 = (i: number, j: number) => `t0_${i}_${j}`;
/** Distance (m) from (x,z) to the footprint of L0 tile `id`. */
function distToL0(id: string, x: number, z: number): number {
  const t = parse(id);
  if (!t) return Infinity;
  const x0 = t.i * VISUAL_L0, z0 = t.j * VISUAL_L0;
  return Math.hypot(Math.max(x0 - x, 0, x - (x0 + VISUAL_L0)), Math.max(z0 - z, 0, z - (z0 + VISUAL_L0)));
}
function parse(id: string): { level: number; i: number; j: number } | null {
  const m = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(id);
  return m ? { level: +m[1], i: +m[2], j: +m[3] } : null;
}

/** Published 500 m tiles that have no published L0 child: the tileset shows them as leaves. */
export function leafParents(published: Iterable<string>): Set<string> {
  const ids = new Set(published), out = new Set<string>();
  for (const id of ids) {
    const t = parse(id);
    if (t?.level !== 1) continue;
    const children = [0, 1].flatMap(di => [0, 1].map(dj => l0(2 * t.i + di, 2 * t.j + dj)));
    if (!children.some(c => ids.has(c))) out.add(id);
  }
  return out;
}

/** Unpublished L0 ids wanted at (x,z), nearest first: the 3x3 window around the player's cell, widened to all four
 *  quadrants of a published leaf parent so that parent can be swapped out whole. */
export function wantedTiles(x: number, z: number, published: ReadonlySet<string>, leaves: ReadonlySet<string>): string[] {
  const ci = Math.floor(x / VISUAL_L0), cj = Math.floor(z / VISUAL_L0);
  const want = new Set<string>();
  for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) {
    if (published.has(l0(i, j))) continue;
    want.add(l0(i, j));
    const parent = `t1_${Math.floor(i / 2)}_${Math.floor(j / 2)}`;
    if (!leaves.has(parent)) continue;
    const pi = Math.floor(i / 2), pj = Math.floor(j / 2);
    for (const di of [0, 1]) for (const dj of [0, 1]) want.add(l0(2 * pi + di, 2 * pj + dj));
  }
  return [...want].map(id => ({ id, d: distToL0(id, x, z) })).sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1))
    .map(t => t.id);
}

/** The 16 collision cells (62.5 m) of L0 tile `id`. */
export function cellsOf(id: string): string[] {
  const t = parse(id);
  if (!t || t.level !== 0) return [];
  const per = VISUAL_L0 / COLLISION_TILE;
  return Array.from({ length: per * per }, (_, k) => `c_${t.i * per + (k % per)}_${t.j * per + Math.floor(k / per)}`);
}

/** The draft's collision is missing or unreadable: the tile is not drawn. */
class DraftRejected extends Error {}

/** A published leaf parent is hidden while every one of its quadrants is resident as a draft or swapped tile. */
export function parentCovered(parent: string, resident: ReadonlySet<string>): boolean {
  const t = parse(parent);
  if (t?.level !== 1) return false;
  return [0, 1].every(di => [0, 1].every(dj => resident.has(l0(2 * t.i + di, 2 * t.j + dj))));
}

interface CoarseFragment { coarse: true; tile: string; visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry> }
type State = 'pending' | 'loading' | 'draft' | 'published' | 'failed';
interface Entry { state: State; model?: THREE.Object3D; collision: string[]; nextPoll: number; error?: string }

export interface CoarseHost {
  scene: THREE.Scene;
  renderer: THREE.WebGPURenderer;
  /** The live published manifest; draft collision entries are added to and removed from `collision`. */
  manifest: Manifest;
  manifestUrl: string;
  /** Published tileset renderer: its leaf parents are hidden under complete drafts. */
  tiles: THREE.EventDispatcher<{ 'load-model': { scene: THREE.Object3D; tile: { extras?: Record<string, unknown> } };
    'dispose-model': { scene: THREE.Object3D; tile: { extras?: Record<string, unknown> } } }>;
  unloadCollision: (id: string) => void;
  /** Decodes a draft .col in the physics worker (PhysicsClient.checkTile); rejects when the worker cannot use it. */
  checkCollision: (buffer: ArrayBuffer) => Promise<unknown>;
  style?: (model: THREE.Object3D) => void;
  unstyle?: (model: THREE.Object3D) => void;
  fetch?: typeof fetch;
  loadModel?: (url: string) => Promise<THREE.Object3D>;
  /** Parent of the always-visible "draft tile" badge (default document.body; none without a DOM). */
  badgeParent?: HTMLElement;
  /** Page URL that /coarse/ and the manifest resolve against (default location.href). */
  pageUrl?: string;
}

export class CoarseTiles {
  readonly entries = new Map<string, Entry>();
  private published: Set<string>;
  private leaves: Set<string>;
  private parents = new Map<string, THREE.Object3D>();
  private nextCheck = 0;
  private nextManifest = -1;
  private fetch: typeof fetch;
  private load: (url: string) => Promise<THREE.Object3D>;
  status = '';
  private clock = 0;
  private badge: HTMLDivElement | null = null;
  private page: string;

  /** The service answers /coarse/status only on a local dev server with the companion running. */
  static async start(host: CoarseHost): Promise<CoarseTiles | null> {
    const page = new URL(host.pageUrl ?? location.href);
    if (!isLocalHost(page.hostname)) return null;
    try {
      const res = await (host.fetch ?? fetch)(new URL(`${COARSE_ROOT}status`, page).href, { signal: AbortSignal.timeout(2000) });
      // A static host without the service answers with HTML or {"state":"offline"}.
      if (!res.ok || (await res.json() as { state?: string }).state === 'offline') return null;
    } catch { return null; }
    return new CoarseTiles(host);
  }

  constructor(private host: CoarseHost) {
    this.fetch = host.fetch ?? fetch.bind(globalThis);
    this.published = new Set(Object.keys(host.manifest.visual));
    this.leaves = leafParents(this.published);
    this.load = host.loadModel ?? this.gltfLoader();
    this.page = host.pageUrl ?? location.href;
    const parent = host.badgeParent ?? (typeof document === 'undefined' ? null : document.body);
    if (parent) this.badge = parent.appendChild(document.createElement('div'));
    if (this.badge) Object.assign(this.badge.style, { position: 'fixed', top: '8px', left: '50%', transform: 'translateX(-50%)',
      padding: '3px 10px', borderRadius: '4px', background: 'rgba(20,24,32,0.72)', color: '#ffd98a',
      font: '12px/1.4 system-ui, sans-serif', pointerEvents: 'none', zIndex: '20', display: 'none' });
    host.tiles.addEventListener('load-model', ({ scene, tile }) => {
      const id = tile.extras?.id;
      if (typeof id === 'string' && this.leaves.has(id)) { this.parents.set(id, scene); this.updateParents(); }
    });
    host.tiles.addEventListener('dispose-model', ({ tile }) => {
      const id = tile.extras?.id;
      if (typeof id === 'string') this.parents.delete(id);
    });
  }

  private gltfLoader() {
    const ktx2 = new KTX2Loader().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(this.host.renderer);
    const loader = new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    return async (url: string) => (await loader.loadAsync(url)).scene;
  }

  /** Call every frame with the player position; work is throttled to once per second (`now` in ms). */
  update(x: number, z: number, now = performance.now()) {
    this.clock = now;
    if (this.nextManifest < 0) this.nextManifest = now + MANIFEST_POLL_MS;
    if (now >= this.nextManifest) { this.nextManifest = now + MANIFEST_POLL_MS; void this.refreshManifest(); }
    if (now < this.nextCheck) return;
    this.nextCheck = now + POLL_MS;
    const wanted = wantedTiles(x, z, this.published, this.leaves);
    for (const id of wanted) {
      const e = this.entries.get(id) ?? { state: 'pending' as State, collision: [], nextPoll: 0 };
      this.entries.set(id, e);
      if ((e.state === 'pending' || e.state === 'failed') && now >= e.nextPoll) void this.poll(id, e, distToL0(id, x, z));
    }
    for (const [id, e] of this.entries) {
      if (wanted.includes(id) || distToL0(id, x, z) <= KEEP_M) continue;
      this.drop(e);
      this.entries.delete(id);
    }
    const here = `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
    const states = [...this.entries.values()];
    const building = states.filter(e => e.state === 'pending' || e.state === 'loading').length;
    const drafts = states.filter(e => e.state === 'draft').length;
    const at = this.entries.get(here);
    this.status = at?.state === 'draft' ? `draft tile ${here} (coarse; ${drafts} resident, ${building} building)`
      : at?.state === 'published' ? `published tile ${here} streamed in place of its draft (reload for LOD)`
        : at ? `draft tile ${here} ${at.state === 'failed' ? `unavailable: ${at.error}` : 'building...'}`
          : building ? `draft tiles: ${drafts} resident, ${building} building` : '';
    if (this.badge) { this.badge.textContent = this.status; this.badge.style.display = this.status ? '' : 'none'; }
  }

  private async poll(id: string, e: Entry, d: number) {
    e.state = 'loading';
    try {
      const res = await this.fetch(new URL(`${COARSE_ROOT}${id}/manifest.json?d=${Math.round(d)}`, this.page).href, { cache: 'no-store' });
      if (res.status === 202) { e.state = 'pending'; e.nextPoll = this.clock + 2000; return; }
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { error?: string };
        e.state = 'failed'; e.error = body.error ?? `HTTP ${res.status}`; e.nextPoll = this.clock + RETRY_MS;
        return;
      }
      const fragment = await res.json() as CoarseFragment;
      if (!fragment.coarse || fragment.tile !== id) throw new Error(`${id}: not a coarse tile fragment`);
      const base = new URL(`${COARSE_ROOT}${id}/`, this.page);
      // Collision before visibility: every cell of the tile needs a collision entry (a published one wins), and every
      // draft entry must decode in the physics worker. A draft that fails is never drawn; the edge walls
      // (physics/edgeWalls.ts) keep the player out of it and the HUD names the reason.
      const own = Object.entries(fragment.collision).filter(([cid]) => !this.host.manifest.collision[cid]);
      const uncovered = cellsOf(id).filter(cid => !fragment.collision[cid] && !this.host.manifest.collision[cid]);
      if (uncovered.length) throw new DraftRejected(`no collision for ${uncovered.length} of 16 cells (${uncovered[0]})`);
      await Promise.all(own.map(async ([cid, entry]) => {
        const col = await this.fetch(new URL(entry.url, base).href, { cache: 'no-store' });
        if (!col.ok) throw new DraftRejected(`collision ${cid}: HTTP ${col.status}`);
        const buf = await col.arrayBuffer();
        if (buf.byteLength !== entry.bytes) throw new DraftRejected(`collision ${cid}: ${buf.byteLength} bytes, manifest ${entry.bytes}`);
        await this.host.checkCollision(buf).catch((err: Error) => { throw new DraftRejected(`collision ${cid}: ${err.message}`); });
      }));
      const model = await this.load(new URL(fragment.visual[id].url, base).href);
      if (this.entries.get(id) !== e) { this.dispose(model); return; } // dropped while loading
      this.host.style?.(model);
      tint(model, this.host.renderer);
      this.host.scene.add(model);
      e.model = model;
      for (const [cid, entry] of own) {
        this.host.manifest.collision[cid] = { ...entry, url: new URL(entry.url, base).href };
        e.collision.push(cid);
      }
      e.state = 'draft';
      this.updateParents();
    } catch (err) {
      const rejected = err instanceof DraftRejected;
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + (rejected ? RETRY_MS : 5000);
      if (rejected) e.error = (err as Error).message;
      console.warn(`[coarse] ${id}: ${(err as Error).message}`);
    }
  }

  /** A new release's tileset now renders `published` (world/releaseSwap.ts): drafts and streamed copies of those
   *  tiles leave, and the draft window and leaf parents follow the new list. */
  release(published: Iterable<string>) {
    this.published = new Set(published);
    this.leaves = leafParents(this.published);
    for (const [id, e] of this.entries) {
      if (!this.published.has(id)) continue;
      this.drop(e);
      this.entries.delete(id);
    }
    this.updateParents();
  }

  /** Re-read the published manifest; a drafted tile that is published now is swapped for the published one. */
  private async refreshManifest() {
    try {
      const res = await this.fetch(new URL(this.host.manifestUrl, this.page).href, { cache: 'no-store' });
      if (!res.ok) return;
      const fresh = await res.json() as Manifest;
      const base = new URL('.', new URL(this.host.manifestUrl, this.page));
      for (const [id, e] of this.entries) {
        const visual = fresh.visual[id];
        if (!visual || e.state === 'published') continue;
        const model = await this.load(new URL(visual.url, base).href);
        this.drop(e);
        this.host.style?.(model);
        this.host.scene.add(model);
        e.model = model;
        for (const [cid, entry] of Object.entries(fresh.collision)) {
          const p = /^c_(-?\d+)_(-?\d+)$/.exec(cid);
          if (!p || l0(Math.floor(+p[1] / 4), Math.floor(+p[2] / 4)) !== id) continue;
          this.host.manifest.collision[cid] = { ...entry, url: new URL(entry.url, base).href };
          e.collision.push(cid);
        }
        e.state = 'published';
        this.published.add(id);
      }
      this.updateParents();
    } catch (err) {
      console.warn(`[coarse] manifest refresh: ${(err as Error).message}`);
    }
  }

  private drop(e: Entry) {
    if (e.model) { this.host.unstyle?.(e.model); e.model.removeFromParent(); this.dispose(e.model); e.model = undefined; }
    for (const cid of e.collision) { delete this.host.manifest.collision[cid]; this.host.unloadCollision(cid); }
    e.collision = [];
    if (e.state === 'draft' || e.state === 'published') e.state = 'pending';
    this.updateParents();
  }

  private dispose(model: THREE.Object3D) {
    model.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        for (const v of Object.values(m)) if ((v as THREE.Texture)?.isTexture) (v as THREE.Texture).dispose();
        m.dispose();
      }
    });
  }

  private updateParents() {
    const resident = new Set([...this.entries].filter(([, e]) => e.state === 'draft' || e.state === 'published').map(([id]) => id));
    for (const [id, scene] of this.parents) scene.visible = !parentCovered(id, resident);
  }
}

/** Draft look: 55 % saturation with a slight cool cast; drawn just in front of coplanar published terrain. */
function tint(model: THREE.Object3D, renderer: THREE.WebGPURenderer) {
  const cache = new Map<THREE.Material, THREE.Material>();
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.castShadow = true; mesh.receiveShadow = true;
    const style = (source: THREE.Material) => {
      if (cache.has(source)) return cache.get(source)!;
      const node = ((source as THREE.NodeMaterial).isNodeMaterial ? source : renderer.library.fromMaterial(source.clone())) as THREE.MeshStandardNodeMaterial;
      node.colorNode = saturation(node.colorNode ?? materialColor.rgb, 0.55).mul(vec3(0.94, 0.97, 1.04));
      node.polygonOffset = true; node.polygonOffsetFactor = -1; node.polygonOffsetUnits = -2;
      cache.set(source, node);
      return node;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
  });
}
