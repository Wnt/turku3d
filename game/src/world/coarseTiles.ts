// Draft service is the only world source. Each visible tile owns all sixteen collision cells.
// Requests are checked against the current generator version; resident tiles swap only after
// their replacement geometry and collision have been validated.
import { splitCollisionBundle, type CollisionBundle } from './collisionBundle';
import { statusBadges } from '../ui/statusBadges';
import { textureUploads } from '../render/textureUploads';
import { routeAheadTiles, draftDemand, arrivalToL0, aheadTiles, cellsOf, distToL0, startupDraftTiles, wantedTiles } from './coarseRegion';
export { aheadTiles, cellsOf, wantedTiles } from './coarseRegion';
import * as THREE from 'three/webgpu';
import { tintDraft } from '../render/facadeKit';
import { SharedMaterials, disposeDraw } from '../render/sharedMaterials';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { LoadingKtx } from './loadingKtx';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { VISUAL_L0 } from '../core/origin';
import { isLocalHost } from '../core/localOnly';
import { draftsUrl } from '../net/config';
import type { Manifest, ManifestEntry, ParkedVehicle } from './manifest';
import type { RoadGraphJson } from '../gameplay/roadGraph';

const POLL_MS = 1000;
/** Retry transient failures promptly; the service owns its build-failure backoff. */
const RETRY_MS = 5000;
/** Speed above which a jump is a teleport (m/s). */
const MAX_SPEED = 100;
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
  collision_bundle?: CollisionBundle;
  roads?: { url: string; bytes: number } | null; vehicles?: ParkedVehicle[]; vehicle_models?: Manifest['vehicle_models'] }
/** `built`: the service has it but it is not loaded (a prefetched tile outside the 3x3 window). */
type State = 'pending' | 'loading' | 'built' | 'draft' | 'failed';
interface Entry {
  state: State; model?: THREE.Object3D; nextPoll: number; error?: string; roads?: RoadGraphJson;
  /** The collision entries this tile put into the manifest (dropped only while they are still there). */
  collision: Map<string, ManifestEntry>;
  vehicles?: ParkedVehicle[];
  /** The service's estimate (clock ms) of when a queued or building tile is built. */
  readyAt?: number;
  /** The service's build counter at this entry's last poll. */
  version?: number; visualHash?: string; refreshing?: boolean;
  /** Visible requests own a model/collision pair; ahead requests own only service metadata. */
  resident?: boolean;
}

export interface CoarseHost {
  scene: THREE.Scene;
  renderer: THREE.WebGPURenderer;
  /** Runtime residency inventory populated by draft fragments. */
  manifest: Manifest;
  unloadCollision: (id: string) => void;
  /** Loaded collision tiles whose manifest entry changed are fetched again and swapped (CollisionStreamer.refresh). */
  refreshCollision: (ids: string[]) => void;
  /** Decodes a draft .col in the physics worker (PhysicsClient.checkTile); rejects when the worker cannot use it. */
  checkCollision: (buffer: ArrayBuffer) => Promise<unknown>;
  /** Retain validated bytes for the distance streamer; no second HTTP fetch to build colliders. */
  retainCollision?: (id: string, entry: ManifestEntry, buffer: ArrayBuffer) => void;
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
  private nextCheck = 0;
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
  private client = `draft-${globalThis.crypto.randomUUID()}`;
  private demandBusy = false;
  private demandSequence = 0;
  private routeHint: readonly { x: number; z: number }[] | null = null;
  private materials = new SharedMaterials();

  /** Optional remaining mission route, engine metres, ordered forward. Clear with null. Stays client-side. */
  setRouteHint(points: readonly { x: number; z: number }[] | null) {
    this.routeHint = points?.filter(p => Number.isFinite(p.x) && Number.isFinite(p.z)).map(p => ({ ...p })) ?? null;
    this.nextCheck = 0;
  }
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
    const coarse = new CoarseTiles(host);
    if (!status || status === slow || status.state === 'offline') coarse.down();
    return coarse;
  }

  constructor(private host: CoarseHost) {
    this.fetch = host.fetch ?? fetch.bind(globalThis);
    this.load = host.loadModel ?? this.gltfLoader();
    this.page = host.pageUrl ?? location.href;
    const page = new URL(this.page);
    this.root = draftsUrl(page) ?? '/coarse/';
    this.remote = !isLocalHost(page.hostname);
    const parent = host.badgeParent ?? (typeof document === 'undefined' ? null : document.body);
    if (parent) this.badge = statusBadges(parent).appendChild(Object.assign(document.createElement('div'), { className: 'draft-tile-badge' }));
    if (this.badge) Object.assign(this.badge.style, { padding: '3px 10px', borderRadius: '4px', background: 'rgba(20,24,32,0.72)', color: '#ffd98a',
      font: '12px/1.4 system-ui, sans-serif', pointerEvents: 'none', zIndex: '20', display: 'none' });

  }

  private gltfLoader() {
    // Without workers, decodeGltfBufferAsync runs WASM in a main-thread microtask. The singleton
    // shares two decoder workers across tile loads and the character instead of accumulating decode bursts.
    MeshoptDecoder.useWorkers(2);
    const ktx2 = new LoadingKtx().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(this.host.renderer);
    const loader = new GLTFLoader().register(() => textureUploads(this.host.renderer).plugin()).setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    return async (url: string) => (await loader.loadAsync(url)).scene;
  }

  retainedStarts: readonly { x: number; z: number }[] = [];
  private startup: { points: readonly { x: number; z: number }[]; radius: number } | null = null;
  /** Preload actual starting ground; distant draft expansion joins ordinary streaming after placement. */
  preload(points: readonly { x: number; z: number }[], radius: number): () => void {
    this.startup = { points, radius };
    return () => { this.startup = null; this.nextCheck = 0; };
  }
  private wantedStarts(x: number, z: number) {
    if (this.startup) return startupDraftTiles(this.startup.points, this.startup.radius);
    return [...new Set([{ x, z }, ...this.retainedStarts].flatMap(p => wantedTiles(p.x, p.z)))];
  }
  private async prepareModel(id: string, entry: Entry, model: THREE.Object3D) {
    const state = entry.state;
    let ready = false;
    try {
      await this.host.style?.(model);
      if (this.entries.get(id) !== entry || entry.state !== state) return false;
      tintDraft(model, this.host.renderer, this.materials);
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
    if (now < this.nextCheck) return;
    this.nextCheck = now + POLL_MS;
    this.track(x, z, now);
    const byArrival = (a: string, b: string) => arrivalToL0(a, x, z, this.velocity.x, this.velocity.z)
      - arrivalToL0(b, x, z, this.velocity.x, this.velocity.z) || distToL0(a, x, z) - distToL0(b, x, z);
    const wanted = this.wantedStarts(x, z).sort(byArrival);
    const route = this.routeHint && routeAheadTiles(this.routeHint, x, z, Math.hypot(this.velocity.x, this.velocity.z));
    const ahead = (route?.tiles ?? aheadTiles(x, z, this.velocity.x, this.velocity.z)).filter(id => !wanted.includes(id)).sort(byArrival);
    // Load the 3x3 window; retain already requested models for another 75 m beyond
    // its maximum axis distance. Boundary jitter cannot repeatedly discard/rebuild
    // a column. The retained square still bounds residency to at most 4x4 per start.
    const retain = (id: string) => [{ x, z }, ...this.retainedStarts].some(point => {
      const match = /^t0_(-?\d+)_(-?\d+)$/.exec(id);
      if (!match) return false;
      const x0 = +match[1] * VISUAL_L0, z0 = +match[2] * VISUAL_L0;
      return Math.max(x0 - point.x, point.x - x0 - VISUAL_L0,
        z0 - point.z, point.z - z0 - VISUAL_L0) <= VISUAL_L0 + 75;
    });
    // Deleting an entry also invalidates in-flight preparation.
    for (const [id, e] of this.entries) {
      if (wanted.includes(id) || (e.resident && retain(id)) || (!e.resident && ahead.includes(id))) continue;
      this.drop(e); this.entries.delete(id);
    }
    if (this.outage) {
      if (now >= this.outage.nextProbe && !this.outage.probing) void this.probe();
      this.show(this.remote ? 'drafts: remote service offline' : RESTARTING);
      return;
    }
    void this.want(x, z);
    if (this.remote) void this.sendDemand(draftDemand(wanted, ahead, x, z, this.velocity.x, this.velocity.z, route?.urgent));
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
      : at ? `draft tile ${here}${at.state === 'failed' ? ` retrying: ${at.error}` : waiting}` : '';
    const next = soonest !== undefined && at?.state === 'draft' ? `next tile in ${soonest} s` : '';
    this.show([head, loaded.length ? `drafts ${loaded.join(' ')}` : '', building ? `${building} building` : '', queueFull ? 'remote queue full; retrying' : '', next]
      .filter(Boolean).join(' · '));
  }

  /** Startup waits for the requested draft ground on both localhost and Pages. A draft whose build failed does not
   *  block startup: it is left out and warned about once, and the world starts without that tile. */
  progressAt(x: number, z: number) {
    const points = [{ x, z }, ...this.retainedStarts, ...this.startup?.points ?? []];
    const critical = new Set(startupDraftTiles(points, 3));
    const wanted = this.wantedStarts(x, z).filter(id => {
      const entry = this.entries.get(id);
      if (entry?.state !== 'failed' || critical.has(id)) return true;
      if (!this.skipped.has(id)) { this.skipped.add(id); console.warn(`draft ${id} failed, starting without it: ${entry.error}`); }
      return false;
    });
    const ready = wanted.filter(id => this.entries.get(id)?.state === 'draft');
    return { loaded: ready.length, total: wanted.length, errors: [] as string[] };
  }
  private skipped = new Set<string>();

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
      if (e.state === 'draft') continue;
      e.state = 'pending'; e.nextPoll = 0; e.error = undefined; e.readyAt = undefined;
    }
    console.info('[coarse] service back: drafts resume');
  }

  /** A /coarse/* request; ServiceDown when the service did not answer. */
  private async request(url: string): Promise<Response> {
    let res: Response;
    const href = new URL(url, this.page).href;
    const immutable = /\.[0-9a-f]{64}\.(?:col|glb|json)(?:$|\?)/.test(href);
    try { res = await this.fetch(href, { cache: immutable ? 'default' : 'no-cache', signal: AbortSignal.timeout(30_000) }); }
    catch { throw new ServiceDown(); }
    if (gatewayDown(res.status)) throw new ServiceDown();
    return res;
  }

  private entry(id: string): Entry {
    let e = this.entries.get(id);
    if (!e) this.entries.set(id, e = { state: 'pending', collision: new Map(), nextPoll: 0 });
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

  /** A replaced want-list releases old tiles immediately; a lost tab expires on the service. */
  private async sendDemand(demand: ReturnType<typeof draftDemand>) {
    if (this.demandBusy) return;
    this.demandBusy = true;
    const sequence = ++this.demandSequence;
    const q = new URLSearchParams({ client: this.client, tiles: demand.tiles.join(','),
      urgent: demand.urgent.join(','), visible: demand.visible.join(',') });
    try {
      const res = await this.request(`${this.root}demand?${q}`);
      if (!res.ok) return;
      const body = await res.json() as { version?: number };
      if (sequence === this.demandSequence && typeof body.version === 'number' && body.version !== this.version) {
        this.version = body.version;
        this.nextCheck = 0;
      }
    } catch { this.down(); }
    finally { this.demandBusy = false; }
  }

  /** Keep resident collision and geometry until an upgraded draft is fully ready. */
  private async refreshDraft(id: string, e: Entry, d: number) {
    e.refreshing = true; e.version = this.version; e.nextPoll = this.clock + 2000;
    try {
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(d)}${this.remote ? `&client=${this.client}` : ''}`);
      if (!res.ok || res.status === 202) return;
      const fragment = await res.json() as CoarseFragment;
      await this.applyFragment(id, e, fragment);
    } catch (err) {
      if (err instanceof ServiceDown) this.down();
      else console.warn(`[coarse] ${id} facade refresh: ${(err as Error).message}`);
    } finally { e.refreshing = false; }
  }

  /** Commit a validated visual/collision pair together after model preparation. */
  private async applyFragment(id: string, e: Entry, fragment: CoarseFragment) {
    const state = e.state;
    if (!fragment.coarse || fragment.tile !== id || !fragment.visual?.[id]) throw new DraftRejected(`${id}: invalid draft fragment`);
    const visualChanged = !e.model || fragment.visual[id].sha256 !== e.visualHash;
    const missing = cellsOf(id).filter(cid => !fragment.collision[cid]);
    if (missing.length) throw new DraftRejected(`no collision for ${missing.length} of 16 cells (${missing[0]})`);
    const base = new URL(`${this.root}${id}/`, this.page);
    const changed = Object.entries(fragment.collision).filter(([cid, entry]) => !e.collision.has(cid) || entry.sha256 !== e.collision.get(cid)?.sha256);
    const buffers = new Map<string, ArrayBuffer>();
    const validate = async () => {
      let bundled: Map<string, ArrayBuffer> | undefined;
      if (changed.length && fragment.collision_bundle) {
        const response = await this.request(new URL(fragment.collision_bundle.url, base).href);
        if (!response.ok) throw new DraftRejected(`collision bundle: HTTP ${response.status}`);
        bundled = await splitCollisionBundle(await response.arrayBuffer(), fragment.collision_bundle, fragment.collision);
      }
      await Promise.all(changed.map(async ([cid, entry]) => {
        let buf = bundled?.get(cid);
        if (!buf) {
          const col = await this.request(new URL(entry.url, base).href);
          if (!col.ok) throw new DraftRejected(`collision ${cid}: HTTP ${col.status}`);
          buf = await col.arrayBuffer();
        }
        if (buf.byteLength !== entry.bytes) throw new DraftRejected(`collision ${cid}: size changed`);
        await this.host.checkCollision(this.host.retainCollision ? buf.slice(0) : buf)
          .catch((err: Error) => { throw new DraftRejected(`collision ${cid}: ${err.message}`); });
        if (this.host.retainCollision) buffers.set(cid, buf);
      }));
    };
    // Fetch/decode geometry while the physics worker validates collision. Visibility still commits atomically.
    const [validation, geometry, streets] = await Promise.allSettled([
      validate(), visualChanged ? this.load(new URL(fragment.visual[id].url, base).href) : Promise.resolve(null),
      fragment.roads ? this.request(new URL(fragment.roads.url, base).href)
        .then(r => r.ok ? r.json() as Promise<RoadGraphJson> : null) : Promise.resolve(null),
    ]);
    const model = geometry.status === 'fulfilled' ? geometry.value : null;
    const failure = [validation, geometry, streets].find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') { if (model) this.dispose(model); throw failure.reason; }
    if (this.entries.get(id) !== e || e.state !== state) { if (model) this.dispose(model); return; }
    const roads = streets.status === 'fulfilled' ? streets.value : null;
    if (!visualChanged) {
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
      this.takeVehicles(e, fragment, base);
      if (roads || e.roads) { e.roads = roads ?? undefined; this.onMapChange?.(); }
      return;
    }
    if (!model) throw new DraftRejected(`${id}: missing visual model`);
    if (!await this.prepareModel(id, e, model)) return;
    // Detach before attaching: scene hooks must never observe both passes.
    const old = e.model;
    old?.removeFromParent();
    this.host.scene.add(model);
    e.model = model; e.visualHash = fragment.visual[id].sha256;
    this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
    e.state = 'draft';
    this.takeVehicles(e, fragment, base);
    if (old) { this.host.untrackModel?.(old, id); this.host.unstyle?.(old); old.removeFromParent(); this.dispose(old); }
    if (roads || e.roads) { e.roads = roads ?? undefined; this.onMapChange?.(); }
    this.host.trackModel?.(model, id);
  }

  private async poll(id: string, e: Entry, d: number, load: boolean) {
    e.resident = load;
    e.state = 'loading';
    e.version = this.version;
    try {
      const query = `?d=${Math.round(d)}${this.remote ? `&client=${this.client}` : ''}`;
      let res = await this.request(`${this.root}${id}/collision.json${query}`);
      // Older producers remain usable while a source deployment is switching.
      if (res.status === 404) res = await this.request(`${this.root}${id}/manifest.json${query}`);
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
      let fragment = await res.json() as CoarseFragment;
      // A legacy collision-only response cannot expose invisible obstacles.
      if (!fragment.visual?.[id]) {
        res = await this.request(`${this.root}${id}/manifest.json${query}`);
        if (res.status === 202) { e.state = 'pending'; e.nextPoll = this.clock + 2000; return; }
        if (!res.ok) throw new DraftRejected(`${id}: no visible draft`);
        fragment = await res.json() as CoarseFragment;
      }
      if (!fragment.coarse || fragment.tile !== id || !fragment.visual?.[id]) throw new DraftRejected(`${id}: no visible draft`);
      if (!load) { e.state = 'built'; e.nextPoll = 0; return; }
      await this.applyFragment(id, e, fragment);
      e.nextPoll = this.clock + 2000;
    } catch (err) {
      if (err instanceof ServiceDown) { e.state = 'pending'; e.nextPoll = 0; this.down(); return; }
      const rejected = err instanceof DraftRejected;
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + (rejected ? RETRY_MS : 5000);
      if (rejected) e.error = (err as Error).message;
      console.warn(`[coarse] ${id}: ${(err as Error).message}`);
    }
  }

  /** Draft L0 tiles currently drawn. */
  drawn(): string[] {
    return [...this.entries].filter(([, e]) => e.model).map(([id]) => id);
  }

  /** Street graphs of the drafts in the scene, for the map and the router (gameplay/index.ts StreetMap). */
  roadGraphs(): RoadGraphJson[] {
    return [...this.entries.values()].flatMap(e => e.state === 'draft' && e.roads ? [e.roads] : []);
  }

  /** Publish the tile's validated collision and refresh loaded cells in place. */
  private take(e: Entry, entries: [string, ManifestEntry][], buffers = new Map<string, ArrayBuffer>()) {
    const swapped: string[] = [];
    for (const [cid, entry] of entries) {
      const current = this.host.manifest.collision[cid];
      if (current) swapped.push(cid);
      this.host.manifest.collision[cid] = entry;
      e.collision.set(cid, entry);
      const buffer = buffers.get(cid);
      if (buffer) this.host.retainCollision?.(cid, entry, buffer);
    }
    if (swapped.length) this.host.refreshCollision(swapped);
  }

  private takeVehicles(e: Entry, fragment: CoarseFragment, base: URL) {
    const old = new Set(e.vehicles ?? []);
    e.vehicles = fragment.vehicles ?? [];
    const incoming = new Set(e.vehicles.map(v => v.object_id));
    this.host.manifest.vehicles = [...(this.host.manifest.vehicles ?? []).filter(v => !old.has(v) && !incoming.has(v.object_id)), ...e.vehicles];
    this.host.manifest.vehicle_models ??= {};
    for (const [key, asset] of Object.entries(fragment.vehicle_models ?? {})) {
      this.host.manifest.vehicle_models[key] = { ...asset, url: new URL(asset.url, base).href };
    }
  }

  private drop(e: Entry) {
    const oldVehicles = new Set(e.vehicles ?? []);
    this.host.manifest.vehicles = this.host.manifest.vehicles?.filter(v => !oldVehicles.has(v));
    e.vehicles = undefined;
    if (e.model) {
      const id = [...this.entries].find(([, entry]) => entry === e)?.[0];
      if (id) this.host.untrackModel?.(e.model, id);
      this.host.unstyle?.(e.model); e.model.removeFromParent(); this.dispose(e.model); e.model = undefined;
    }
    for (const [cid, entry] of e.collision) {
      // An asynchronous replacement may already own the cell.
      if (this.host.manifest.collision[cid] !== entry) continue;
      delete this.host.manifest.collision[cid];
      this.host.unloadCollision(cid);
    }
    e.collision.clear();
    if (e.roads) { e.roads = undefined; this.onMapChange?.(); }
    if (e.state === 'draft') e.state = 'pending';
  }

  private dispose(model: THREE.Object3D) {
    const materials = new Set<THREE.Material>(), geometries = new Set<THREE.BufferGeometry>(), textures = new Set<THREE.Texture>();
    model.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      disposeDraw(mesh); geometries.add(mesh.geometry);
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        materials.add(m);
        for (const v of Object.values(m)) if ((v as THREE.Texture)?.isTexture) textures.add(v as THREE.Texture);
      }
    });
    for (const geometry of geometries) geometry.dispose();
    for (const texture of textures) texture.dispose();
    this.materials.releaseModel(model, materials);
  }

}
