// Coarse (draft) tiles on localhost: when the player comes within one tile of an L0 cell the published tileset lacks,
// the companion service (tools/dev/coarse-tiles, proxied at /coarse/) builds it in seconds and this module streams it
// in like a published tile: the GLB goes into the scene, its .col entries into the manifest so CollisionStreamer
// loads them with the normal residency rules. Collision comes first: a draft is drawn only when all 16 of its cells
// have collision and the physics worker has decoded each of its .col files; one that fails is never drawn (the
// physics edge walls keep the player out of cells without loaded collision). Draft tiles get a desaturated tint and a HUD line. Published 500 m
// leaf tiles are hidden once all four of their quadrants are drafted (the same REPLACE rule as the tileset).
// Ahead of a moving player the cells within the next 30 s of travel (plus a one-tile ring) are only asked to be built,
// so at full speed the service (four builds at once) has them ready before they enter the 3x3 window and load.
// Every 60 s the published manifest is re-read; a tile that has been published there replaces its draft.
// Once a second the player's position and velocity go to /coarse/want, so the service prefetches the raw data ahead
// of the player; a tile whose data is still downloading answers 202 pending-data and is polled like a queued one.
// A landing restarts the service (tools/dev/localhost/coarse.mjs): a connection error (the dev server's 503) or a 404
// from a route the running service lacks pauses the drafts with one console line and the badge `drafts: service
// restarting`; only /coarse/status is probed until the service answers again, so a restart is no console error spam.
// The /coarse/want answer carries the service's build counter: when it moves, the waiting tiles are polled at once, so a finished
// draft arrives without waiting for its own poll; queued tiles carry an ETA that the badge shows.
// Never active off localhost (GitHub Pages) or when the service is not running.
import * as THREE from 'three/webgpu';
import { materialColor, saturation, vec3 } from 'three/tsl';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { COLLISION_TILE, VISUAL_L0 } from '../core/origin';
import { isLocalHost } from '../core/localOnly';
import type { Manifest, ManifestEntry } from './manifest';
import type { RoadGraphJson } from '../gameplay/roadGraph';

const COARSE_ROOT = '/coarse/';
const POLL_MS = 1000;
const MANIFEST_POLL_MS = 60_000;
/** A failed or rejected draft is asked for again after this (ms); the service retries failed builds after 120 s. */
const RETRY_MS = 120_000;
/** Prefetch horizon (s of travel at the current speed) and the speed above which a jump is a teleport (m/s). */
const PREFETCH_SECONDS = 30, MAX_SPEED = 100;
/** Drafts farther than this (m, to the tile footprint) are unloaded. */
const KEEP_M = 2 * VISUAL_L0;
/** While the service restarts, /coarse/status is probed this often (ms). */
const PROBE_MS = 2000;
export const RESTARTING = 'drafts: service restarting';

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
/** The service did not answer (connection error, or the dev server's 502-504 for it): it is restarting. */
class ServiceDown extends Error {}
const gatewayDown = (status: number) => status === 502 || status === 503 || status === 504;

/** `offline`: the service did not answer; back once /coarse/status does. `outdated`: it answered 404 on a route the
 *  game uses (a landing added it and the old process still runs); back once /coarse/status names another `started`
 *  than the first probe saw, or after RETRY_MS. */
interface Outage { reason: 'offline' | 'outdated'; started?: number | null; nextProbe: number; until: number; probing: boolean }

/** Unpublished L0 ids to build ahead of a player moving at (vx, vz) m/s: every cell within the next
 *  PREFETCH_SECONDS of travel along the heading plus a one-tile ring, nearest first. Empty below walking speed. */
export function aheadTiles(x: number, z: number, vx: number, vz: number, published: ReadonlySet<string>): string[] {
  const speed = Math.hypot(vx, vz);
  if (speed < 1) return [];
  const reach = speed * PREFETCH_SECONDS, steps = Math.ceil(reach / (VISUAL_L0 / 2));
  const want = new Set<string>();
  for (let k = 0; k <= steps; k++) {
    const px = x + vx / speed * reach * k / steps, pz = z + vz / speed * reach * k / steps;
    const ci = Math.floor(px / VISUAL_L0), cj = Math.floor(pz / VISUAL_L0);
    for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) if (!published.has(l0(i, j))) want.add(l0(i, j));
  }
  return [...want].map(id => ({ id, d: distToL0(id, x, z) })).sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)).map(t => t.id);
}

/** A published leaf parent is hidden while every one of its quadrants is resident as a draft or swapped tile. */
export function parentCovered(parent: string, resident: ReadonlySet<string>): boolean {
  const t = parse(parent);
  if (t?.level !== 1) return false;
  return [0, 1].every(di => [0, 1].every(dj => resident.has(l0(2 * t.i + di, 2 * t.j + dj))));
}

interface CoarseFragment { coarse: true; tile: string; visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry>;
  /** The tile's OSM street graph (absent without an OSM extract). */
  roads?: { url: string; bytes: number } | null }
/** `built`: the service has it but it is not loaded (a prefetched tile outside the 3x3 window). */
type State = 'pending' | 'loading' | 'built' | 'draft' | 'published' | 'failed';
interface Entry {
  state: State; model?: THREE.Object3D; collision: string[]; nextPoll: number; error?: string; roads?: RoadGraphJson;
  /** The service's estimate (clock ms) of when a queued or building tile is built. */
  readyAt?: number;
  /** The service's build counter at this entry's last poll. */
  version?: number;
}

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
  private last: { x: number; z: number; t: number } | null = null;
  /** Called when the drafts' street graphs change (a draft arrived or left) and after each 60 s manifest poll. */
  onMapChange: (() => void) | null = null;
  /** Smoothed ground velocity (m/s) of the player, for the prefetch. */
  velocity = { x: 0, z: 0 };
  /** The service's build counter (from /coarse/want): when it moves, waiting tiles are polled at once. */
  private version = -1;
  private page: string;
  /** Set while the service restarts: no tile or prefetch requests, only status probes. */
  outage: Outage | null = null;

  /** The service answers /coarse/status only on a local dev server with the companion running, or when the
   *  localhost game service supervises it and it is restarting right now. */
  static async start(host: CoarseHost): Promise<CoarseTiles | null> {
    const page = new URL(host.pageUrl ?? location.href);
    if (!isLocalHost(page.hostname)) return null;
    const get = (url: string) => (host.fetch ?? fetch)(new URL(url, page).href, { signal: AbortSignal.timeout(2000) })
      .then(r => r.ok ? r.json() as Promise<Record<string, unknown>> : null).catch(() => null);
    // A static host without the service answers with HTML or {"state":"offline"}.
    const status = await get(`${COARSE_ROOT}status`);
    if (!status) return null;
    if (status.state !== 'offline') return new CoarseTiles(host);
    const local = await get('/__localhost/status') as { coarse?: { state?: string } } | null;
    if (!local?.coarse?.state || local.coarse.state === 'off') return null;
    const coarse = new CoarseTiles(host);
    coarse.down('offline');
    return coarse;
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
    this.track(x, z, now);
    if (this.outage) {
      if (now >= this.outage.nextProbe && !this.outage.probing) void this.probe();
      this.show(RESTARTING);
      return;
    }
    void this.want(x, z);
    const wanted = wantedTiles(x, z, this.published, this.leaves);
    const ahead = aheadTiles(x, z, this.velocity.x, this.velocity.z, this.published).filter(id => !wanted.includes(id));
    const due = (e: Entry) => (e.state === 'pending' || e.state === 'failed' || e.state === 'built')
      && (now >= e.nextPoll || (e.state !== 'built' && (e.version ?? this.version) < this.version));
    for (const id of wanted) {
      const e = this.entry(id);
      if (due(e)) void this.poll(id, e, distToL0(id, x, z), true);
    }
    // Ahead of a moving player only the build is requested; the tile loads once it enters the 3x3 window.
    for (const id of ahead) {
      const e = this.entry(id);
      if (due(e) && e.state !== 'built') void this.poll(id, e, distToL0(id, x, z), false);
    }
    for (const [id, e] of this.entries) {
      if (wanted.includes(id) || ahead.includes(id) || distToL0(id, x, z) <= KEEP_M) continue;
      this.drop(e);
      this.entries.delete(id);
    }
    const here = `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
    const states = [...this.entries.values()];
    const building = states.filter(e => e.state === 'pending' || e.state === 'loading').length;
    const loaded = [...this.entries].filter(([, e]) => e.state === 'draft').map(([id]) => id).sort();
    const at = this.entries.get(here);
    const inSeconds = (e?: Entry) => e?.readyAt === undefined ? null : Math.max(0, Math.ceil((e.readyAt - now) / 1000));
    const ready = inSeconds(at);
    const waiting = at?.state === 'pending' && at.error ? ` ${at.error}` : ready !== null ? ` ready in ${ready} s` : ' building...';
    const soonest = wanted.map(id => this.entries.get(id)).filter(e => e?.state === 'pending' && !e.error)
      .map(inSeconds).filter((v): v is number => v !== null).sort((a, b) => a - b)[0];
    const head = at?.state === 'draft' ? `draft tile ${here}`
      : at?.state === 'published' ? `published tile ${here} streamed in place of its draft (reload for LOD)`
        : at ? `draft tile ${here}${at.state === 'failed' ? ` unavailable: ${at.error}` : waiting}` : '';
    const next = soonest !== undefined && at?.state === 'draft' ? `next tile in ${soonest} s` : '';
    this.show([head, loaded.length ? `drafts ${loaded.join(' ')}` : '', building ? `${building} building` : '', next]
      .filter(Boolean).join(' · '));
  }

  private show(status: string) {
    this.status = status;
    if (this.badge) { this.badge.textContent = status; this.badge.style.display = status ? '' : 'none'; }
  }

  /** The service is restarting: one console line, then only status probes until it answers (`probe`). */
  down(reason: Outage['reason']) {
    if (this.outage) return;
    // An outdated service is probed at once, so the probe sees the old process's start time.
    this.outage = { reason, nextProbe: reason === 'outdated' ? 0 : this.clock + PROBE_MS, until: this.clock + RETRY_MS, probing: false };
    console.warn(`[coarse] ${RESTARTING}: drafts paused until it answers again`);
    this.show(RESTARTING);
    if (reason === 'outdated') void this.probe();
  }

  private async probe() {
    const outage = this.outage;
    if (!outage) return;
    outage.probing = true;
    const status = await this.fetch(new URL(`${COARSE_ROOT}status`, this.page).href, { cache: 'no-store' })
      .then(r => r.ok ? r.json() as Promise<{ state?: string; started?: number }> : null).catch(() => null);
    outage.probing = false;
    outage.nextProbe = this.clock + PROBE_MS;
    if (this.outage !== outage || !status || status.state === 'offline') return;
    if (outage.reason === 'outdated' && this.clock < outage.until) {
      const started = status.started ?? null;
      if (outage.started === undefined) { outage.started = started; return; }
      if (started === outage.started) return;
    }
    this.outage = null;
    this.nextCheck = 0;
    for (const e of this.entries.values()) if (e.state === 'loading') { e.state = 'pending'; e.nextPoll = 0; }
    console.info('[coarse] service back: drafts resume');
  }

  /** A /coarse/* request; ServiceDown when the service did not answer. */
  private async request(url: string): Promise<Response> {
    let res: Response;
    try { res = await this.fetch(new URL(url, this.page).href, { cache: 'no-store' }); }
    catch { throw new ServiceDown(); }
    if (gatewayDown(res.status)) throw new ServiceDown();
    return res;
  }

  private entry(id: string): Entry {
    let e = this.entries.get(id);
    if (!e) this.entries.set(id, e = { state: 'pending', collision: [], nextPoll: 0 });
    return e;
  }

  /** Ground velocity from the positions of successive checks; a jump faster than MAX_SPEED is a teleport. */
  private track(x: number, z: number, now: number) {
    const last = this.last;
    this.last = { x, z, t: now };
    if (!last || now <= last.t) return;
    const dt = (now - last.t) / 1000, vx = (x - last.x) / dt, vz = (z - last.z) / dt;
    if (Math.hypot(vx, vz) > MAX_SPEED) { this.velocity = { x: 0, z: 0 }; return; }
    this.velocity = { x: 0.5 * this.velocity.x + 0.5 * vx, z: 0.5 * this.velocity.z + 0.5 * vz };
  }

  /** Position and velocity for the service's raw-data prefetch (pipeline/turku3d/coarse/prefetch.py). */
  private async want(x: number, z: number) {
    const q = [x, z, this.velocity.x, this.velocity.z].map(v => v.toFixed(1));
    try {
      const res = await this.request(`${COARSE_ROOT}want?x=${q[0]}&z=${q[1]}&vx=${q[2]}&vz=${q[3]}`);
      if (res.status === 404) { this.down('outdated'); return; }
      const { version } = await res.json().catch(() => ({})) as { version?: number };
      if (typeof version !== 'number' || version === this.version) return;
      // A build finished: the next frame polls every tile last polled before it (see `due`). The first answer only
      // sets the baseline.
      if (this.version < 0) for (const e of this.entries.values()) e.version = version;
      else this.nextCheck = 0;
      this.version = version;
    } catch { this.down('offline'); }
  }

  private async poll(id: string, e: Entry, d: number, load: boolean) {
    e.state = 'loading';
    e.version = this.version;
    try {
      const res = await this.request(`${COARSE_ROOT}${id}/manifest.json?d=${Math.round(d)}`);
      if (res.status === 202) {
        const body = await res.json().catch(() => ({})) as { state?: string; missing?: string[]; eta?: number };
        e.readyAt = typeof body.eta === 'number' ? this.clock + 1000 * body.eta : undefined;
        e.error = body.state === 'pending-data' ? `waiting for ${[...new Set(body.missing?.map(u => u.split('/')[0]))].join(', ')} data`
          : undefined;
        e.state = 'pending'; e.nextPoll = this.clock + 2000; return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { error?: string };
        e.state = 'failed'; e.error = body.error ?? `HTTP ${res.status}`; e.nextPoll = this.clock + RETRY_MS;
        return;
      }
      if (!load) { e.state = 'built'; e.nextPoll = 0; return; }
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
        const col = await this.request(new URL(entry.url, base).href);
        if (!col.ok) throw new DraftRejected(`collision ${cid}: HTTP ${col.status}`);
        const buf = await col.arrayBuffer();
        if (buf.byteLength !== entry.bytes) throw new DraftRejected(`collision ${cid}: ${buf.byteLength} bytes, manifest ${entry.bytes}`);
        await this.host.checkCollision(buf).catch((err: Error) => { throw new DraftRejected(`collision ${cid}: ${err.message}`); });
      }));
      const roads = fragment.roads ? await this.fetch(new URL(fragment.roads.url, base).href, { cache: 'no-store' })
        .then(r => r.ok ? r.json() as Promise<RoadGraphJson> : null)
        .catch((err: Error) => { console.warn(`[coarse] ${id} streets: ${err.message}`); return null; }) : null;
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
      if (roads) { e.roads = roads; this.onMapChange?.(); }
    } catch (err) {
      if (err instanceof ServiceDown) { e.state = 'pending'; e.nextPoll = 0; this.down('offline'); return; }
      const rejected = err instanceof DraftRejected;
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + (rejected ? RETRY_MS : 5000);
      if (rejected) e.error = (err as Error).message;
      console.warn(`[coarse] ${id}: ${(err as Error).message}`);
    }
  }

  /** Street graphs of the drafts in the scene, for the map and the router (gameplay/index.ts StreetMap). */
  roadGraphs(): RoadGraphJson[] {
    return [...this.entries.values()].flatMap(e => e.state === 'draft' && e.roads ? [e.roads] : []);
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
    this.onMapChange?.();
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
      this.onMapChange?.();
    } catch (err) {
      console.warn(`[coarse] manifest refresh: ${(err as Error).message}`);
    }
  }

  private drop(e: Entry) {
    if (e.model) { this.host.unstyle?.(e.model); e.model.removeFromParent(); this.dispose(e.model); e.model = undefined; }
    for (const cid of e.collision) { delete this.host.manifest.collision[cid]; this.host.unloadCollision(cid); }
    e.collision = [];
    if (e.roads) { e.roads = undefined; this.onMapChange?.(); }
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
