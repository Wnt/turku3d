// Draft service is the only world source. Each visible tile owns all sixteen collision cells.
// Requests are checked against the current generator version; resident tiles swap only after
// their replacement geometry and collision have been validated.
import { recordFreshness } from './coarseFreshness';
import { VisualPassComparison } from './visualPass';
import { ScreenshotScenery } from './screenshotScenery';
import { splitCollisionBundle } from './collisionBundle';
import { statusBadges } from '../ui/statusBadges';
import { textureUploads } from '../render/textureUploads';
import { routeAheadTiles, draftDemand, arrivalToL0, aheadTiles, cellsOf, distToL0, preparationTiles, retainDraftTile, startupDraftTiles, wantedTiles } from './coarseRegion';
export { aheadTiles, cellsOf, wantedTiles } from './coarseRegion';
import * as THREE from 'three/webgpu';
import { tintDraft } from '../render/facadeKit';
import { SharedMaterials } from '../render/sharedMaterials';
import { disposeModelResources } from './disposeModel';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { loadGlb } from './loadGlb';
import { sharedKtx } from './loadingKtx';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { VISUAL_L0 } from '../core/origin';
import { FLY } from '../gameplay/flyMode';
import { isLocalHost } from '../core/localOnly';
import { draftsUrl } from '../net/config';
import { ServiceDown, gatewayDown, backoff, retryAfter } from '../net/draftRetry';
import type { ManifestEntry } from './manifest';
import type { CoarseFragment, CoarseHost, Entry } from './coarseContracts';
export type { CoarseHost } from './coarseContracts';
import type { RoadGraphJson } from '../gameplay/roadGraph';

const POLL_MS = 1000;
/** Retry transient failures promptly; the service owns its build-failure backoff. */
const RETRY_MS = 5000;
/** Allow the fastest real movement plus sampling jitter before treating a jump as a teleport. */
const MAX_SPEED = FLY.fastSpeed * 1.25;
/** While the service restarts, /coarse/status is probed this often (ms). */
const PROBE_MS = 2000;
/** A probe the busy service has not answered within this (ms) counts as no answer. */
const PROBE_TIMEOUT_MS = 10_000;
export const RESTARTING = 'drafts: service restarting';

/** The draft's collision is missing or unreadable: the tile is not drawn. */
class DraftRejected extends Error {}

/** The service did not answer: paused until /coarse/status answers (any process, any producer code). */
interface Outage { nextProbe: number; probing: boolean; attempts: number }


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
  private demandNext = 0;
  private demandRetries = 0;
  private demandSequence = 0;
  private routeHint: readonly { x: number; z: number }[] | null = null;
  private preparing = new Set<string>();
  private details = 0;
  private materials = new SharedMaterials();
  private comparison: VisualPassComparison;
  readonly screenshotScenery: ScreenshotScenery;
  get visualPass() { return this.comparison.pass; }
  get comparisonStatus() { return this.comparison.status; }
  setVisualPass(pass: 'auto' | 'first' | 'full') { this.comparison.setPass(pass); }
  private compare(id: string, e: Entry) { this.comparison.clock = this.clock; return this.comparison.compare(id, e); }

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
    this.comparison = new VisualPassComparison(host, this.entries, url => this.request(url), url => this.load(url),
      model => tintDraft(model, this.host.renderer, this.materials), model => this.dispose(model, 'comparison'), this.root, this.page);
    this.screenshotScenery = new ScreenshotScenery(host, this.entries, url => this.request(url), url => this.load(url),
      model => tintDraft(model, this.host.renderer, this.materials), (model, id) => this.dispose(model, id), this.root, this.page);
    const parent = host.badgeParent ?? (typeof document === 'undefined' ? null : document.body);
    if (parent) this.badge = statusBadges(parent).appendChild(Object.assign(document.createElement('div'), { className: 'draft-tile-badge' }));
    if (this.badge) Object.assign(this.badge.style, { padding: '3px 10px', borderRadius: '4px', background: 'rgba(20,24,32,0.72)', color: '#ffd98a',
      font: '12px/1.4 system-ui, sans-serif', pointerEvents: 'none', zIndex: '20', display: 'none' });

  }

  private gltfLoader() {
    // Without workers, decodeGltfBufferAsync runs WASM in a main-thread microtask. The singleton
    // shares two decoder workers across tile loads and the character instead of accumulating decode bursts.
    MeshoptDecoder.useWorkers(2);
    const ktx2 = sharedKtx(this.host.renderer);
    const loader = new GLTFLoader().register(() => textureUploads(this.host.renderer).plugin()).setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    return async (url: string) => (await loadGlb(loader, url)).scene;
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
    return [...new Set([{ x, z }, ...this.retainedStarts].flatMap(p => wantedTiles(p.x, p.z, this.host.detailRadius)))];
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
      if (!ready) this.dispose(model);
    }
  }

  /** Call every frame with the player position; scan at least four times per crossed tile (`now` in ms). */
  update(x: number, z: number, now = performance.now(), motion?: { x: number; z: number }) {
    this.clock = now;
    // Screenshot downloads refill free slots each frame, independently of world-generation polling.
    this.screenshotScenery.update(x, z, now, !!this.outage);
    if (motion && Number.isFinite(motion.x) && Number.isFinite(motion.z)) {
      if (Math.hypot(motion.x - this.velocity.x, motion.z - this.velocity.z) > 20) this.nextCheck = now;
      this.velocity = { x: motion.x, z: motion.z };
    }
    if (now < this.nextCheck) return;
    this.nextCheck = now + Math.min(POLL_MS, VISUAL_L0 / Math.max(1, Math.hypot(this.velocity.x, this.velocity.z)) * 250);
    if (!motion) this.track(x, z, now);
    const byArrival = (a: string, b: string) => arrivalToL0(a, x, z, this.velocity.x, this.velocity.z)
      - arrivalToL0(b, x, z, this.velocity.x, this.velocity.z) || distToL0(a, x, z) - distToL0(b, x, z);
    const wanted = this.wantedStarts(x, z).sort(byArrival);
    const route = this.routeHint && routeAheadTiles(this.routeHint, x, z, Math.hypot(this.velocity.x, this.velocity.z));
    const ahead = (route?.tiles ?? aheadTiles(x, z, this.velocity.x, this.velocity.z)).filter(id => !wanted.includes(id)).sort(byArrival);
    const lookahead = this.startup ? [] : preparationTiles(wanted, ahead, x, z, this.velocity.x, this.velocity.z, route?.urgent);
    const retain = (id: string) => this.host.detailRadius === undefined
      ? retainDraftTile(id, [{ x, z }, ...this.retainedStarts])
      : [{ x, z }, ...this.retainedStarts].some(p => distToL0(id, p.x, p.z) <= this.host.detailRadius! + 40);
    // Deleting an entry also invalidates in-flight preparation.
    for (const [id, e] of this.entries) {
      // A distant full model must not remain pinned by the lightweight surface reserve. Re-admit it
      // as a collision preview below, retaining full detail only near the current mobile player.
      if (this.host.detailRadius !== undefined && !this.startup && e.model && !e.preview && !wanted.includes(id) && !retain(id)) {
        this.drop(e); this.entries.delete(id); continue;
      }
      if (wanted.includes(id) || lookahead.includes(id) || (e.resident && retain(id)) || (!e.resident && ahead.includes(id))) continue;
      this.drop(e); this.entries.delete(id);
    }
    if (this.outage) {
      if (now >= this.outage.nextProbe && !this.outage.probing) void this.probe();
      this.show(this.remote ? 'drafts: remote service offline' : RESTARTING);
      return;
    }
    for (const [id, e] of this.entries) void this.compare(id, e);
    void this.want(x, z);
    if (this.remote) {
      const demand = draftDemand([...wanted, ...lookahead], ahead, x, z, this.velocity.x, this.velocity.z, route?.urgent);
      if (this.screenshotScenery.radius) demand.tiles = [...new Set([...demand.tiles, ...this.screenshotScenery.demand()])].slice(0, 128);
      void this.sendDemand(demand);
    }
    const due = (e: Entry) => (e.state === 'pending' || e.state === 'failed' || e.state === 'built')
      && (now >= e.nextPoll || (!e.retries && e.state !== 'built' && (e.version ?? this.version) < this.version));
    for (const id of wanted) {
      const e = this.entry(id);
      if (due(e)) void this.poll(id, e, distToL0(id, x, z), true);
      else if (e.state === 'draft' && !e.preview && !e.refreshing && (now >= e.nextPoll || (!e.retries && (e.version ?? -1) < this.version)))
        void this.refreshDraft(id, e, distToL0(id, x, z));
    }
    // Core requests always start first. This separate slot limits speculative preparation only.
    for (const id of lookahead) {
      if (this.preparing.size >= 4 || this.preparing.has(id)) continue;
      const e = this.entry(id), distance = distToL0(id, x, z);
      const task = due(e) ? this.poll(id, e, distance, true) : null;
      if (task) {
        this.preparing.add(id);
        void task.finally(() => this.preparing.delete(id));
      }
    }
    // Detailed facades/textures cannot monopolize the network while ground is still arriving.
    for (const id of wanted) {
      const e = this.entries.get(id);
      if (e?.preview && !e.refreshing && this.details < 2 && now >= e.nextPoll) {
        e.refreshing = true; this.details++;
        void this.applyFragment(id, e, e.preview).catch(error => this.warn(`[coarse] ${id} detail: ${error}`))
          .finally(() => { e.refreshing = false; e.nextPoll = this.clock + 2000; this.details--; });
      }
    }
    // All other ahead tiles request builds without loading model or collision resources.
    for (const id of ahead) {
      if (lookahead.includes(id)) continue;
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
      if (!this.skipped.has(id)) { this.skipped.add(id); this.warn(`draft ${id} failed, starting without it: ${entry.error}`); }
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
  private nextWarning = -Infinity;
  private outageFailures = 0;
  private warn(message: string) {
    if (this.clock < this.nextWarning) return;
    this.nextWarning = this.clock + 30_000;
    console.warn(message);
  }

  down(error?: ServiceDown) {
    if (this.outage) return;
    this.outage = { nextProbe: this.clock + Math.max(this.remote ? backoff(this.outageFailures++) : PROBE_MS, error?.retryAfter ?? 0), probing: false, attempts: 0 };
    const message = this.remote ? 'drafts: remote service offline' : RESTARTING;
    this.warn(`[coarse] ${message}: drafts paused until it answers again`);
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
      .then(r => {
        outage.nextProbe = this.clock + Math.max(this.remote ? backoff(outage.attempts++) : PROBE_MS, retryAfter(r));
        return r.ok ? r.json() as Promise<{ state?: string }> : null;
      }).catch(() => { outage.nextProbe = this.clock + (this.remote ? backoff(outage.attempts++) : PROBE_MS); return null; });
    outage.probing = false;
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
    try { res = await this.fetch(href, { priority: 'high', cache: immutable ? 'default' : 'no-cache', signal: AbortSignal.timeout(30_000) }); }
    catch { throw new ServiceDown(); }
    if (gatewayDown(res.status)) throw new ServiceDown(retryAfter(res));
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
    } catch (err) { this.down(err instanceof ServiceDown ? err : undefined); }
  }

  /** A replaced want-list releases old tiles immediately; a lost tab expires on the service. */
  private async sendDemand(demand: ReturnType<typeof draftDemand>) {
    if (this.demandBusy || this.clock < this.demandNext) return;
    this.demandBusy = true;
    this.demandNext = this.clock + POLL_MS;
    const sequence = ++this.demandSequence;
    const q = new URLSearchParams({ client: this.client, tiles: demand.tiles.join(','),
      urgent: demand.urgent.join(','), visible: demand.visible.join(',') });
    try {
      const res = await this.request(`${this.root}demand?${q}`);
      if (res.status === 429) {
        this.demandNext = this.clock + Math.max(retryAfter(res), backoff(this.demandRetries++));
        return;
      }
      if (!res.ok) return;
      this.demandRetries = 0;
      const body = await res.json() as { version?: number };
      if (sequence === this.demandSequence && typeof body.version === 'number' && body.version !== this.version) {
        this.version = body.version;
        this.nextCheck = 0;
      }
    } catch (err) { this.down(err instanceof ServiceDown ? err : undefined); }
    finally { this.demandBusy = false; }
  }

  /** Keep resident collision and geometry until an upgraded draft is fully ready. */
  private async refreshDraft(id: string, e: Entry, d: number) {
    e.refreshing = true; e.version = this.version; e.nextPoll = this.clock + 2000;
    try {
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(d)}${this.remote ? `&client=${this.client}` : ''}`);
      if (res.status === 429) {
        e.nextPoll = this.clock + Math.max(retryAfter(res), backoff(e.retries = (e.retries ?? 0) + 1));
        return;
      }
      if (!res.ok || res.status === 202) return;
      const fragment = await res.json() as CoarseFragment;
      await this.applyFragment(id, e, fragment);
      e.retries = 0; this.outageFailures = 0;
    } catch (err) {
      if (err instanceof ServiceDown) this.down(err);
      else this.warn(`[coarse] ${id} facade refresh: ${(err as Error).message}`);
      e.nextPoll = this.clock + backoff(e.retries = (e.retries ?? 0) + 1);
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
        await this.host.checkCollision(this.host.retainCollision || this.host.previewCollision ? buf.slice(0) : buf)
          .catch((err: Error) => { throw new DraftRejected(`collision ${cid}: ${err.message}`); });
        if (this.host.retainCollision || this.host.previewCollision) buffers.set(cid, buf);
      }));
    };
    if (!e.model && !this.startup && this.host.previewCollision) {
      await validate();
      if (this.entries.get(id) !== e || e.state !== state) return;
      const model = await this.host.previewCollision(buffers);
      if (this.entries.get(id) !== e || e.state !== state) { this.dispose(model); return; }
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
      e.model = model; e.preview = fragment; e.pass = fragment.pass_kind; e.state = 'draft';
      recordFreshness(e, fragment); this.host.scene.add(model);
      return;
    }
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
      recordFreshness(e, fragment);
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
      this.takeVehicles(e, fragment, base);
      if (roads || e.roads) { e.roads = roads ?? undefined; this.onMapChange?.(); }
      return;
    }
    if (!model) throw new DraftRejected(`${id}: missing visual model`);
    if (!await this.prepareModel(id, e, model)) return;
    recordFreshness(e, fragment);
    // Detach before attaching: scene hooks must never observe both passes.
    const old = e.model;
    old?.removeFromParent();
    this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
    this.host.scene.add(model);
    this.comparison.clear(e); e.pass = fragment.pass_kind;
    e.model = model; model.visible = this.visualPass === 'auto' || e.pass === this.visualPass; e.visualHash = fragment.visual[id].sha256;
    e.state = 'draft'; e.preview = undefined;
    this.takeVehicles(e, fragment, base);
    if (old) this.dispose(old, id);
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
        e.nextPoll = this.clock + Math.max(retryAfter(res), backoff(e.retries = (e.retries ?? 0) + 1));
        return;
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
      e.retries = 0; this.outageFailures = 0;
      e.nextPoll = this.clock + 2000;
    } catch (err) {
      if (err instanceof ServiceDown) { e.state = 'pending'; e.nextPoll = this.clock + backoff(e.retries = (e.retries ?? 0) + 1); this.down(err); return; }
      const rejected = err instanceof DraftRejected;
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + Math.max(rejected ? RETRY_MS : 0, backoff(e.retries = (e.retries ?? 0) + 1));
      if (rejected) e.error = (err as Error).message;
      this.warn(`[coarse] ${id}: ${(err as Error).message}`);
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
    this.comparison.clear(e);
    const oldVehicles = new Set(e.vehicles ?? []);
    this.host.manifest.vehicles = this.host.manifest.vehicles?.filter(v => !oldVehicles.has(v));
    e.vehicles = undefined;
    if (e.model) {
      const id = [...this.entries].find(([, entry]) => entry === e)?.[0];
      this.dispose(e.model, id); e.model = undefined;
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

  private dispose(model: THREE.Object3D, id?: string) {
    model.removeFromParent();
    if (id) this.host.untrackModel?.(model, id);
    const release = () => {
      this.host.unstyle?.(model);
      disposeModelResources(model, this.materials);
    };
    if (this.host.retire) this.host.retire(release); else release();
  }

}
