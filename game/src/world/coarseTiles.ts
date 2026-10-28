import { statusBadges } from '../ui/statusBadges';
import { textureUploads } from '../render/textureUploads';
// Coarse (draft) tiles: when the player comes within one tile of an L0 cell the published tileset lacks,
// the local companion or Pages' remote service builds it and this module streams it
// in like a published tile: the GLB goes into the scene, its .col entries into the manifest so CollisionStreamer
// loads them with the normal residency rules. Collision comes first: a draft is drawn only when all 16 of its cells
// have collision and the physics worker has decoded each of its .col files; one that fails is never drawn (the
// physics edge walls keep the player out of cells without loaded collision). A drawn draft's collision is used for
// all 16 of its cells, also where the release publishes a margin cell there (the ring of ground and old margin props
// around the playable tiles): collision follows what is drawn, and the published entry returns when the draft leaves.
// Draft tiles get a desaturated tint and a HUD line. Published 500 m
// leaf tiles are hidden once all four of their quadrants are drafted (the same REPLACE rule as the tileset).
// Ahead of a moving player the cells within the next 30 s of travel (plus a one-tile ring) are only asked to be built,
// so at full speed the service (four builds at once) has them ready before they enter the 3x3 window and load.
// Every 60 s the published manifest is re-read; a tile that has been published there replaces its draft.
// Once a second the player's position and velocity go to /coarse/want, so the service prefetches the raw data ahead
// of the player; a tile whose data is still downloading answers 202 pending-data and is polled like a queued one.
// A landing restarts the service (tools/dev/localhost/coarse.mjs): a connection error (the dev server's 503) pauses the
// drafts with one console line and the badge `drafts: service restarting`; only /coarse/status is probed until any
// process answers it, so a restart is no console error spam. Then the ring and the path ahead are asked for at once (a
// new producer rebuilds them on request). A page whose first /coarse/status outlasts its 2 s budget (a busy service)
// starts paused the same way. A 404 from /coarse/want (an older service without the route) only skips the prefetch.
// The /coarse/want answer carries the service's build counter: when it moves, the waiting tiles are polled at once, so a finished
// draft arrives without waiting for its own poll; queued tiles carry an ETA that the badge shows.
// Pages uses the public health probe and tile routes, without sending player motion or requesting service internals.
import { aheadTiles, cellsOf, distToL0, leafParents, parentCovered, wantedTiles } from './coarseRegion';
export { aheadTiles, cellsOf, leafParents, parentCovered, wantedTiles } from './coarseRegion';
import * as THREE from 'three/webgpu';
import { tintDraft } from '../render/facadeKit';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { LoadingKtx } from './loadingKtx';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { VISUAL_L0 } from '../core/origin';
import { isLocalHost } from '../core/localOnly';
import { draftsUrl } from '../net/config';
import type { Manifest, ManifestEntry } from './manifest';
import type { RoadGraphJson } from '../gameplay/roadGraph';

const POLL_MS = 1000;
const MANIFEST_POLL_MS = 60_000;
/** A failed or rejected draft is asked for again after this (ms); the service retries failed builds after 120 s. */
const RETRY_MS = 120_000;
/** Speed above which a jump is a teleport (m/s). */
const MAX_SPEED = 100;
/** Drafts farther than this (m, to the tile footprint) are unloaded. */
const KEEP_M = 2 * VISUAL_L0;
/** While the service restarts, /coarse/status is probed this often (ms). */
const PROBE_MS = 2000;
/** A probe the busy service has not answered within this (ms) counts as no answer. */
const PROBE_TIMEOUT_MS = 10_000;
export const RESTARTING = 'drafts: service restarting';

/** The draft's collision is missing or unreadable: the tile is not drawn. */
class DraftRejected extends Error {}
/** The service did not answer (connection error, or the dev server's 502-504 for it): it is restarting. */
class ServiceDown extends Error {}
const gatewayDown = (status: number) => status === 502 || status === 503 || status === 504;

/** The service did not answer: paused until /coarse/status answers (any process, any producer code). */
interface Outage { nextProbe: number; probing: boolean }

interface CoarseFragment { coarse: true; tile: string; visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry>;
  /** The tile's OSM street graph (absent without an OSM extract). */
  roads?: { url: string; bytes: number } | null }
/** `built`: the service has it but it is not loaded (a prefetched tile outside the 3x3 window). */
type State = 'pending' | 'loading' | 'built' | 'draft' | 'published' | 'failed';
interface Entry {
  state: State; model?: THREE.Object3D; nextPoll: number; error?: string; roads?: RoadGraphJson;
  /** The collision entries this tile put into the manifest (dropped only while they are still there). */
  collision: Map<string, ManifestEntry>;
  /** Published (margin) entries of its cells that its collision replaced, restored when it leaves. */
  replaced: Map<string, ManifestEntry>;
  /** The service's estimate (clock ms) of when a queued or building tile is built. */
  readyAt?: number;
  /** The service's build counter at this entry's last poll. */
  version?: number; visualHash?: string; refreshing?: boolean;
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
  /** Loaded collision tiles whose manifest entry changed are fetched again and swapped (CollisionStreamer.refresh). */
  refreshCollision: (ids: string[]) => void;
  /** Decodes a draft .col in the physics worker (PhysicsClient.checkTile); rejects when the worker cannot use it. */
  checkCollision: (buffer: ArrayBuffer) => Promise<unknown>;
  /** After material styling and draft tint: register derived runtime geometry here. */
  styled?: (model: THREE.Object3D) => void | Promise<void>;
  trackModel?: (model: THREE.Object3D, id: string) => void;
  untrackModel?: (model: THREE.Object3D, id: string) => void;
  style?: (model: THREE.Object3D) => void | Promise<void>;
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
  private building = 0;
  private page: string;
  private root: string;
  private remote: boolean;
  /** Set while the service restarts: no tile or prefetch requests, only status probes. */
  outage: Outage | null = null;

  /** The service answers /coarse/status only on a local dev server with the companion running, or when the
   *  localhost game service supervises it and it is restarting right now. */
  static async start(host: CoarseHost): Promise<CoarseTiles | null> {
    const page = new URL(host.pageUrl ?? location.href);
    const root = draftsUrl(page);
    if (!root) return null;
    const remote = !isLocalHost(page.hostname);
    const slow = Symbol('slow');
    const get = (url: string) => (host.fetch ?? fetch)(new URL(url, page).href, { signal: AbortSignal.timeout(2000) })
      .then(r => r.ok ? r.json() as Promise<Record<string, unknown>> : null)
      .catch((err: Error) => err?.name === 'TimeoutError' ? slow : null);
    // A static host without the service answers with HTML or {"state":"offline"}; only a service keeps it waiting.
    const status = await get(`${root}${remote ? 'health' : 'status'}`);
    if (remote) {
      const coarse = new CoarseTiles(host);
      if (!status || status === slow || status.state === 'offline') coarse.down();
      return coarse;
    }
    if (status === slow) {
      const coarse = new CoarseTiles(host);
      coarse.down();
      return coarse;
    }
    if (!status) return null;
    if (status.state !== 'offline') return new CoarseTiles(host);
    const local = await get('/__localhost/status');
    const supervised = local && local !== slow ? (local as { coarse?: { state?: string } }).coarse?.state : undefined;
    if (!supervised || supervised === 'off') return null;
    const coarse = new CoarseTiles(host);
    coarse.down();
    return coarse;
  }

  constructor(private host: CoarseHost) {
    this.fetch = host.fetch ?? fetch.bind(globalThis);
    this.published = new Set(Object.keys(host.manifest.visual));
    this.leaves = leafParents(this.published);
    this.load = host.loadModel ?? this.gltfLoader();
    this.page = host.pageUrl ?? location.href;
    const page = new URL(this.page);
    this.root = draftsUrl(page) ?? '/coarse/';
    this.remote = !isLocalHost(page.hostname);
    const parent = host.badgeParent ?? (typeof document === 'undefined' ? null : document.body);
    if (parent) this.badge = statusBadges(parent).appendChild(Object.assign(document.createElement('div'), { className: 'draft-tile-badge' }));
    if (this.badge) Object.assign(this.badge.style, { padding: '3px 10px', borderRadius: '4px', background: 'rgba(20,24,32,0.72)', color: '#ffd98a',
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
    const ktx2 = new LoadingKtx().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(this.host.renderer);
    const loader = new GLTFLoader().register(() => textureUploads(this.host.renderer).plugin()).setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    return async (url: string) => (await loader.loadAsync(url)).scene;
  }

  retainedStarts: readonly { x: number; z: number }[] = [];
  private wantedStarts(x: number, z: number) {
    return [...new Set([{ x, z }, ...this.retainedStarts].flatMap(p => wantedTiles(p.x, p.z, this.published, this.leaves)))];
  }
  private async prepareModel(id: string, entry: Entry, model: THREE.Object3D, draft: boolean) {
    const state = entry.state;
    let ready = false;
    try {
      await this.host.style?.(model);
      if (this.entries.get(id) !== entry || entry.state !== state) return false;
      if (draft) tintDraft(model, this.host.renderer);
      await this.host.styled?.(model);
      ready = this.entries.get(id) === entry && entry.state === state;
      return ready;
    } finally {
      if (!ready) { this.host.unstyle?.(model); this.dispose(model); }
    }
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
      this.show(this.remote ? 'drafts: remote service offline' : RESTARTING);
      return;
    }
    void this.want(x, z);
    for (const p of this.retainedStarts) void this.want(p.x, p.z);
    const wanted = this.wantedStarts(x, z);
    const ahead = aheadTiles(x, z, this.velocity.x, this.velocity.z, this.published).filter(id => !wanted.includes(id));
    const due = (e: Entry) => (e.state === 'pending' || e.state === 'failed' || e.state === 'built')
      && (now >= e.nextPoll || (e.state !== 'built' && (e.version ?? this.version) < this.version));
    for (const id of wanted) {
      const e = this.entry(id);
      if (due(e)) void this.poll(id, e, distToL0(id, x, z), true);
      else if (e.state === 'draft' && !e.refreshing && (now >= e.nextPoll || (e.version ?? -1) < this.version))
        void this.refreshDraft(id, e, distToL0(id, x, z));
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
    const building = this.remote ? [...this.entries.values()].filter(e => e.state === 'pending' || e.state === 'loading').length : this.building;
    const queueFull = this.remote && [...this.entries.values()].some(e => e.error === 'queue full');
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
    this.show([head, loaded.length ? `drafts ${loaded.join(' ')}` : '', building ? `${building} building` : '', queueFull ? 'remote queue full; retrying' : '', next]
      .filter(Boolean).join(' · '));
  }

  /** Local startup warms the full window. Remote speculative neighbours must not block a published start offline. */
  progressAt(x: number, z: number) {
    const wanted = this.remote ? [...new Set([{ x, z }, ...this.retainedStarts]
      .map(p => `t0_${Math.floor(p.x / VISUAL_L0)}_${Math.floor(p.z / VISUAL_L0)}`))]
      .filter(id => !this.published.has(id)) : this.wantedStarts(x, z);
    const ready = wanted.filter(id => ['draft', 'published'].includes(this.entries.get(id)?.state ?? ''));
    const errors = wanted.flatMap(id => this.entries.get(id)?.state === 'failed' ? [`draft ${id}: ${this.entries.get(id)!.error}`] : []);
    return { loaded: ready.length, total: wanted.length, errors };
  }

  private show(status: string) {
    this.status = status;
    if (this.badge) { this.badge.textContent = status; this.badge.style.display = status ? '' : 'none'; }
  }

  /** The service is restarting: one console line, then only status probes until it answers (`probe`). */
  down() {
    if (this.outage) return;
    this.outage = { nextProbe: this.clock + PROBE_MS, probing: false };
    const message = this.remote ? 'drafts: remote service offline' : RESTARTING;
    console.warn(`[coarse] ${message}: drafts paused until it answers again`);
    this.show(message);
  }

  /** Any answer from /coarse/status other than the dev server's offline one ends the outage: every tile not drawn is
   *  asked for again at the next frame (a restarted service has no queue, and a new producer rebuilds on request). */
  private async probe() {
    const outage = this.outage;
    if (!outage) return;
    outage.probing = true;
    const status = await this.fetch(new URL(`${this.root}${this.remote ? 'health' : 'status'}`, this.page).href,
      { cache: 'no-store', signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) })
      .then(r => r.ok ? r.json() as Promise<{ state?: string }> : null).catch(() => null);
    outage.probing = false;
    outage.nextProbe = this.clock + PROBE_MS;
    if (this.outage !== outage || !status || status.state === 'offline') return;
    this.outage = null;
    this.nextCheck = 0;
    for (const e of this.entries.values()) {
      if (e.state === 'draft' || e.state === 'published') continue;
      e.state = 'pending'; e.nextPoll = 0; e.error = undefined; e.readyAt = undefined;
    }
    console.info('[coarse] service back: drafts resume');
  }

  /** A /coarse/* request; ServiceDown when the service did not answer. */
  private async request(url: string): Promise<Response> {
    let res: Response;
    try { res = await this.fetch(new URL(url, this.page).href, { cache: 'no-cache', signal: AbortSignal.timeout(30_000) }); }
    catch { throw new ServiceDown(); }
    if (gatewayDown(res.status)) throw new ServiceDown();
    return res;
  }

  private entry(id: string): Entry {
    let e = this.entries.get(id);
    if (!e) this.entries.set(id, e = { state: 'pending', collision: new Map(), replaced: new Map(), nextPoll: 0 });
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
    if (this.remote) return; // Public clients poll tiles independently; no shared player position.
    const q = [x, z, this.velocity.x, this.velocity.z].map(v => v.toFixed(1));
    try {
      const res = await this.request(`${this.root}want?x=${q[0]}&z=${q[1]}&vx=${q[2]}&vz=${q[3]}`);
      if (res.status === 404) return; // an older service without the prefetch route: drafts work without it
      const { version, queued, building } = await res.json().catch(() => ({})) as { version?: number; queued?: number; building?: string[] };
      if (typeof queued === 'number' && Array.isArray(building)) this.building = queued + building.length;
      if (typeof version !== 'number' || version === this.version) return;
      // A build finished: the next frame polls every tile last polled before it (see `due`). The first answer only
      // sets the baseline.
      if (this.version < 0) for (const e of this.entries.values()) e.version = version;
      else this.nextCheck = 0;
      this.version = version;
    } catch { this.down(); }
  }

  /** Keep resident collision and geometry until an upgraded draft is fully ready. */
  private async refreshDraft(id: string, e: Entry, d: number) {
    e.refreshing = true; e.version = this.version; e.nextPoll = this.clock + 15_000;
    try {
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(d)}`);
      if (!res.ok || res.status === 202) return;
      const fragment = await res.json() as CoarseFragment;
      if (!fragment.coarse || fragment.tile !== id || fragment.visual[id]?.sha256 === e.visualHash) return;
      const base = new URL(`${this.root}${id}/`, this.page);
      const changed = Object.entries(fragment.collision).filter(([cid, entry]) => entry.sha256 !== e.collision.get(cid)?.sha256);
      await Promise.all(changed.map(async ([cid, entry]) => {
        const col = await this.request(new URL(entry.url, base).href);
        if (!col.ok) throw new DraftRejected(`collision ${cid}: HTTP ${col.status}`);
        const buf = await col.arrayBuffer();
        if (buf.byteLength !== entry.bytes) throw new DraftRejected(`collision ${cid}: size changed`);
        await this.host.checkCollision(buf);
      }));
      const model = await this.load(new URL(fragment.visual[id].url, base).href);
      if (this.entries.get(id) !== e || e.state !== 'draft') { this.dispose(model); return; }
      if (!await this.prepareModel(id, e, model, true)) return;
      this.host.scene.add(model);
      const old = e.model; e.model = model; e.visualHash = fragment.visual[id].sha256;
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]));
      if (old) { this.host.untrackModel?.(old, id); this.host.unstyle?.(old); old.removeFromParent(); this.dispose(old); }
      this.host.trackModel?.(model, id);
    } catch (err) {
      if (err instanceof ServiceDown) this.down();
      else console.warn(`[coarse] ${id} facade refresh: ${(err as Error).message}`);
    } finally { e.refreshing = false; }
  }

  private async poll(id: string, e: Entry, d: number, load: boolean) {
    e.state = 'loading';
    e.version = this.version;
    try {
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(d)}`);
      if (res.status === 429) {
        e.state = 'pending'; e.error = 'queue full'; e.readyAt = undefined;
        e.nextPoll = this.clock + 5000; return;
      }
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
      const base = new URL(`${this.root}${id}/`, this.page);
      // Collision before visibility: every cell of the tile needs a collision entry (a published margin cell may stand
      // in for a cell the draft lacks), and every draft entry must decode in the physics worker. A draft that fails is
      // never drawn; the edge walls (physics/edgeWalls.ts) keep the player out of it and the HUD names the reason.
      const own = Object.entries(fragment.collision);
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
      if (!await this.prepareModel(id, e, model, true)) return;
      this.host.scene.add(model);
      this.host.trackModel?.(model, id);
      e.model = model;
      this.take(e, own.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]));
      e.state = 'draft'; e.visualHash = fragment.visual[id].sha256; e.nextPoll = this.clock + 15_000;
      this.updateParents();
      if (roads) { e.roads = roads; this.onMapChange?.(); }
    } catch (err) {
      if (err instanceof ServiceDown) { e.state = 'pending'; e.nextPoll = 0; this.down(); return; }
      const rejected = err instanceof DraftRejected;
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + (rejected ? RETRY_MS : 5000);
      if (rejected) e.error = (err as Error).message;
      console.warn(`[coarse] ${id}: ${(err as Error).message}`);
    }
  }

  /** L0 tiles drawn from this module: drafts, and tiles published since the page loaded. */
  drawn(): string[] {
    return [...this.entries].filter(([, e]) => e.state === 'draft' || e.state === 'published').map(([id]) => id);
  }

  /** Street graphs of the drafts in the scene, for the map and the router (gameplay/index.ts StreetMap). */
  roadGraphs(): RoadGraphJson[] {
    return [...this.entries.values()].flatMap(e => e.state === 'draft' && e.roads ? [e.roads] : []);
  }

  /** A new release's tileset now renders `published` (world/releaseSwap.ts): drafts and streamed copies of those
   *  tiles leave, giving their cells to the release's `collision` entries, and the draft window and leaf parents
   *  follow the new list. Drafts that stay keep their cells and return them to the new release when they leave. */
  release(published: Iterable<string>, collision: Record<string, ManifestEntry>) {
    this.published = new Set(published);
    this.leaves = leafParents(this.published);
    for (const [id, e] of this.entries) {
      for (const cid of e.collision.keys()) {
        if (collision[cid]) e.replaced.set(cid, collision[cid]);
        else e.replaced.delete(cid);
      }
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
        if (!await this.prepareModel(id, e, model, false)) continue;
        this.host.scene.add(model);
        this.host.trackModel?.(model, id);
        e.model = model;
        this.take(e, cellsOf(id).filter(cid => fresh.collision[cid])
          .map(cid => [cid, { ...fresh.collision[cid], url: new URL(fresh.collision[cid].url, base).href }]));
        e.state = 'published';
        this.published.add(id);
      }
      this.updateParents();
      this.onMapChange?.();
    } catch (err) {
      console.warn(`[coarse] manifest refresh: ${(err as Error).message}`);
    }
  }

  /** Puts `entries` into the manifest as this tile's collision; a published entry they replace is kept for `drop`,
   *  and loaded colliders of those cells are swapped for the new ones. */
  private take(e: Entry, entries: [string, ManifestEntry][]) {
    const swapped: string[] = [];
    for (const [cid, entry] of entries) {
      const current = this.host.manifest.collision[cid];
      if (current && current !== e.collision.get(cid)) { e.replaced.set(cid, current); swapped.push(cid); }
      else if (current) swapped.push(cid);
      this.host.manifest.collision[cid] = entry;
      e.collision.set(cid, entry);
    }
    if (swapped.length) this.host.refreshCollision(swapped);
  }

  /** Collision ids drafts hold in the manifest: a release swap leaves them to `release`. */
  held(): Set<string> {
    const out = new Set<string>();
    for (const e of this.entries.values()) for (const [cid, entry] of e.collision) if (this.host.manifest.collision[cid] === entry) out.add(cid);
    return out;
  }

  private drop(e: Entry) {
    if (e.model) {
      const id = [...this.entries].find(([, entry]) => entry === e)?.[0];
      if (id) this.host.untrackModel?.(e.model, id);
      this.host.unstyle?.(e.model); e.model.removeFromParent(); this.dispose(e.model); e.model = undefined;
    }
    const restored: string[] = [];
    for (const [cid, entry] of e.collision) {
      // A release swap may have replaced the entry since (world/releaseSwap.ts): then it is no longer this tile's.
      if (this.host.manifest.collision[cid] !== entry) continue;
      const published = e.replaced.get(cid);
      if (published) { this.host.manifest.collision[cid] = published; restored.push(cid); }
      else { delete this.host.manifest.collision[cid]; this.host.unloadCollision(cid); }
    }
    e.collision.clear();
    e.replaced.clear();
    if (restored.length) this.host.refreshCollision(restored);
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
