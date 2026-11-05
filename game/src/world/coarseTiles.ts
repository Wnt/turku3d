// Draft service is the only world source. Each visible tile owns all sixteen collision cells.
// Requests are checked against the current generator version; resident tiles swap only after
// their replacement geometry and collision have been validated.
import { BuildingModelCache, updateBuildingDistances } from './buildingPlacements';
import { shellRoofs, styleBlockLod, type ShellRoof } from '../render/sceneryMaterial';
import { LOD_FADE, lodFades } from '../render/lodFade';
import { streamTrace, tileDistance } from '../telemetry/streamTrace';
import { ownedGeometryBytes } from './modelGeometry';
import { recordFreshness } from './coarseFreshness';
import { acceptsVisualPass, VisualPassComparison } from './visualPass';
import { ScreenshotScenery } from './screenshotScenery';
import { TILE_DETAIL } from './sceneryView';
import { splitCollisionBundle } from './collisionBundle';
import { statusBadges } from '../ui/statusBadges';
import { textureUploaded, textureUploads } from '../render/textureUploads';
import { releaseModelTextureData } from './sharedAssets';
import { routeAheadTiles, draftDemand, arrivalToL0, aheadTiles, cellsOf, distToL0, inViewWedge, preparationTiles, retainDraftTile, retainPreparedTile, startupDraftTiles, viewWedgeTiles, wantedTiles } from './coarseRegion';
export { aheadTiles, cellsOf, wantedTiles } from './coarseRegion';
import * as THREE from 'three/webgpu';
import { tintDraft } from '../render/facadeKit';
import { prepareFacadeModules } from '../render/facadeModules';
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
import { parseStreamingJson, peekStreamingJson, readStreamingJson } from './streamingJson';
import { streamingAllowance, streamingBudget } from '../render/preparationBudget';
import { TileArrays, drawnGeometries, rendererUploaded } from './releaseArrays';
import { GpuMeter, modelGpuBytes, rendererResidency } from './gpuBytes';
import { renderTier, type QualityTier } from '../render/quality';

const POLL_MS = 1000;
/** A leaving full visual draws until scenery covers its square (or none can). Only this far (m) from the camera
 *  is it let go uncovered: a stalled cover never keeps a model alive across the city. */
const HOLD_FAR_M = 1500;
const RETURN_CACHE_TILES = 16;
/** Cached visuals keep their decoded arrays in the JS heap (8-14 MiB of accessors per full tile GLB, plus facades and
 *  merged block shells): the newest cached tiles within this many owned bytes stay, older ones are released. */
export const RETURN_CACHE_BYTES = 96 * 2 ** 20;
/** Hidden full visuals wait this long for a return: driving round a block often comes back within a minute. */
const RETURN_CACHE_MS = 60_000;
/** Ahead-only build acknowledgements own no model or collision resources. Keep them across quick turns. */
const BUILT_CACHE_TILES = 128;
const BUILT_CACHE_MS = 30_000;
/** Byte budget of the final-fragment cache: built acknowledgements, promotions and re-entries reuse these bodies. */
const FRAGMENT_CACHE_BYTES = 16 * 1024 ** 2;
/** While nothing is building, /coarse/want reports the position at most this often (ms) unless the player moved WANT_MOVE_M. */
const WANT_IDLE_MS = 5000;
const WANT_MOVE_M = 50;
/** A driving player prepares full visuals of every tile its 3x3 window will reach within DETAIL_LEAD_S along the
 *  heading, or within DETAIL_LEAD_M, whichever is farther (an install takes several seconds of budgeted work).
 *  Faster flight keeps previews only. */
const DETAIL_LEAD_S = 28;
const DETAIL_LEAD_M = 450;
const DETAIL_LEAD_MAX_M = 700;
const DETAIL_LEAD_MAX_SPEED = 40;
/** Concurrent full-visual installs; tiles ahead and in view may use all but two, which stay free for the current window. */
const DETAIL_SLOTS = 6;
const AHEAD_DETAIL_SLOTS = DETAIL_SLOTS - 2;
/** Concurrent unconfirmed ahead acknowledgements, and concurrent collision previews being prepared. */
const ACK_SLOTS = 4;
const PREVIEW_SLOTS = 6;
/** A preview or load started for the lead stays this long after the lead last covered it: reversals and heading
 *  jitter must not drop and rebuild it. Full visuals leave through the heap-budgeted return cache instead. */
const LEAD_KEEP_MS = 30_000;
/** A lead tile starts its full install only after the lead has covered it this long without a break (heading
 *  jitter), and only when the window is predicted to hold it at least LEAD_MIN_RESIDENCE_S (not a corner clip). */
const LEAD_DWELL_MS = 1500;
/** Tiles in the view wedge dwell less: they are on screen or about to be (a glance shorter than this installs nothing). */
const VIEW_DWELL_MS = 750;
const LEAD_MIN_RESIDENCE_S = 4;
/** Everything in the camera's view wedge within this distance (m) draws full detail: it joins the lead, nearest
 *  first and ahead of the path, under the same dwell rule (a quick look around installs nothing). */
const VIEW_DETAIL_M = 400;
/** A driving camera also counts from where it will be this many seconds ahead (at most VIEW_LEAD_MAX_M); a chosen
 *  view tile stays until it is VIEW_KEEP_M beyond VIEW_DETAIL_M. */
const VIEW_LEAD_S = 8;
const VIEW_LEAD_MAX_M = 200;
const VIEW_KEEP_M = 75;
/** A camera this far above a tile's top (m) is flying: full installs only within HD_3D_M of 3D distance (GTA V's HD
 *  band, techstudy L1/L5), so from 150 m up only a small disc under the camera installs; the scenery tier draws the
 *  rest. Lower cameras keep the ground rules (3x3 window, path and view wedge). */
const HIGH_CAMERA_M = 40;
const HD_3D_M = 250;
/** How often committed tiles release the CPU copies of uploaded render-only arrays (ms). */
const RELEASE_MS = 2000;
/** GPU-byte residency budget per render tier (uploaded scene vertex/index buffers, pooled ones at their slot, plus
 *  textures; world/gpuBytes.ts), for 4 GB cards. Medium and high are unbudgeted. Reduced: winperf's 560X with the
 *  geometry and uniform pools peaks at 2.5 GB in Chrome's GPU process with ~1.25 GB of the card free while the meter
 *  reads 0.7-0.8 GiB, so 1.2 GiB. Over 90 % of it no lead or view tile starts a full install and the return cache
 *  keeps nothing; over it, hidden and far full tiles are dropped (never the current 3x3 window). Measured every
 *  GPU_MEASURE_MS, time-sliced. */
const GPU_BUDGET_BYTES: Partial<Record<QualityTier, number>> = { reduced: 1.2 * 2 ** 30, low: 0.7 * 2 ** 30, mobile: 0.5 * 2 ** 30 };
/** Apple GPUs share the machine's memory (an 8 GB M1 Air classed as reduced): no VRAM wall, but swap once the page
 *  and the GPU process outgrow it (airperf: renderer 4.4 GB + GPU process 3.4 GB, 0.6-1.6 GB swap traffic per drive).
 *  There the budget covers the page's JS heap (ArrayBuffers included) plus its GPU bytes: the GPU share is what
 *  APPLE_MEMORY_BYTES leaves after the heap, within [APPLE_GPU_MIN, APPLE_GPU_MAX]. `?memBudget=GiB` sets the total. */
const APPLE_MEMORY_BYTES = 3.0 * 2 ** 30, APPLE_GPU_MIN = 0.9 * 2 ** 30, APPLE_GPU_MAX = 1.5 * 2 ** 30;
/** Chrome's JS heap including ArrayBuffer backing stores; undefined elsewhere. */
function jsHeapBytes(): number | undefined {
  return (globalThis.performance as unknown as { memory?: { usedJSHeapSize?: number } } | undefined)?.memory?.usedJSHeapSize;
}
const GPU_MEASURE_MS = 3000;
/** A window tile still not loaded after this long (ms) shows as waiting in the tile status. */
const WAITING_MS = 5000;
/** A tile away from the start points that the service is still building stops holding startup after this long (ms):
 *  a city whose service rebuilds its tiles must not keep the player on the loading screen for minutes. */
const START_BUILD_GRACE_MS = 10_000;
/** At load and spawn the start's view wedge within this distance (m) installs full detail with the ground under it,
 *  so the player never starts looking at scenery cells. Half angle of that wedge (rad). */
const START_VIEW_M = 300;
const START_VIEW_HALF = 0.75;
/** An install whose tile has been off the window, the path and the view this long (ms) is abandoned. */
const ABANDON_AFTER_MS = 2000;
/** Retry transient failures promptly; the service owns its build-failure backoff. */
const RETRY_MS = 5000;
/** Allow the fastest real movement plus sampling jitter before treating a jump as a teleport. */
const MAX_SPEED = FLY.fastSpeed * 1.25;
/** While the service restarts, /coarse/status is probed this often (ms). */
const PROBE_MS = 2000;
/** A probe the busy service has not answered within this (ms) counts as no answer. */
const PROBE_TIMEOUT_MS = 10_000;
export const RESTARTING = 'tiles: service restarting';

/** The draft's collision is missing or unreadable: the tile is not drawn. */
class DraftRejected extends Error {}

/** The service did not answer: paused until /coarse/status answers (any process, any producer code). */
interface Outage { nextProbe: number; probing: boolean; attempts: number }


/** Where a start looks from (engine metres) and its horizontal view direction. */
export interface StartView { x: number; z: number; dx: number; dz: number }

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
  /** The service's tile generation (from /coarse/want). A full, non-stale fragment of it is immutable. */
  private generation: string | undefined;
  /** Final fragment bodies by tile, in least-recently-used order. */
  private fragments = new Map<string, { generation: string; hash: string; bytes: number; read: () => Promise<CoarseFragment> }>();
  private fragmentBytes = 0;
  private wantBusy = false;
  private wantNext = 0;
  private wantAt: { x: number; z: number } | null = null;
  private building = 0;
  private page: string;
  private root: string;
  private remote: boolean;
  private client = `draft-${globalThis.crypto.randomUUID()}`;
  /** A named tile client (`?tileClient=canary`) reports itself, so the farm never takes it for a player. */
  private named = false;
  private demandBusy = false;
  private demandNext = 0;
  private demandRetries = 0;
  private demandSequence = 0;
  private routeHint: readonly { x: number; z: number }[] | null = null;
  private preparing = new Set<string>();
  /** Camera height (engine y) for 3D band distances; NaN keeps them horizontal. */
  private viewY = NaN;
  /** Collision previews still drawn under a full model dissolving in over them, released when it lands. */
  private underFade = new Map<THREE.Object3D, { id: string; under: THREE.Object3D }>();
  /** Full visuals that left their square but draw until the scenery cell covers it. */
  private leaving = new Map<THREE.Object3D, { id: string; since: number; finish: () => void }>();
  private builtUntil = new Map<string, number>();
  private details = 0;
  private acking = 0;
  private leadUntil = new Map<string, number>();
  /** The latest scan's lead tiles (nextWindow). */
  private lead: string[] = [];
  /** When each lead tile entered the lead (continuously since), and its predicted window residence. */
  private leadSince = new Map<string, number>();
  private leadInfo = new Map<string, { residence: number; continuing: boolean }>();
  /** When an installing tile left the window, the path and the view. */
  private offPath = new Map<string, number>();
  /** The latest view-wedge tiles (hysteresis for viewTiles). */
  private viewSet = new Set<string>();
  /** The player position of the latest update, for install priority. */
  private at = { x: NaN, z: NaN };
  private materials = new SharedMaterials();
  private buildingModels: BuildingModelCache;
  private shellRoofs = new WeakMap<THREE.Object3D, Map<string, ShellRoof>>();
  private buildingLeases = new WeakMap<THREE.Object3D, () => void>();
  private comparison: VisualPassComparison;
  readonly screenshotScenery: ScreenshotScenery;
  get visualPass() { return this.comparison.pass; }
  get comparisonStatus() { return this.comparison.status; }
  setVisualPass(pass: 'auto' | 'first' | 'full') { this.comparison.setPass(pass); }
  private compare(id: string, e: Entry) {
    if (e.cachedAt !== undefined) return;
    this.comparison.clock = this.clock; return this.comparison.compare(id, e);
  }

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
    if (!host.keepArrays && (host.renderer as unknown as { backend?: unknown })?.backend) this.arrays = new TileArrays(rendererUploaded(host.renderer));
    this.buildingModels = new BuildingModelCache(url => this.load(url), root => disposeModelResources(root, this.materials),
      undefined, undefined, (root, owner, block) => {
        // The HD roofs are cloned into `owner` before its block shells: fit their roof photos once per tile.
        let roofs = this.shellRoofs.get(owner);
        if (!roofs) this.shellRoofs.set(owner, roofs = shellRoofs(owner));
        styleBlockLod(root, owner, this.materials, roofs.get(block));
      });
    this.page = host.pageUrl ?? location.href;
    const page = new URL(this.page);
    this.root = draftsUrl(page) ?? '/coarse/';
    this.remote = !isLocalHost(page.hostname);
    const named = page.searchParams.get('tileClient');
    if (named && /^[a-z0-9-]{1,32}$/.test(named)) { this.client = named; this.named = true; }
    this.comparison = new VisualPassComparison(host, this.entries, url => this.request(url), url => this.load(url),
      model => tintDraft(model, this.host.renderer, this.materials), model => this.dispose(model, 'comparison'), this.root, this.page, (id, fragment) => this.loadFragment(id, fragment));
    // Optional distant derivatives must not put collision/nearby streaming into outage backoff.
    this.screenshotScenery = new ScreenshotScenery(host, this.entries,
      url => this.fetch(new URL(url, this.page).href, { signal: AbortSignal.timeout(30_000) }), url => this.load(url),
      model => tintDraft(model, this.host.renderer, this.materials), (model, id) => this.dispose(model, id), this.root, this.page, this.materials);
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
  private startup: { points: readonly { x: number; z: number }[]; radius: number; view?: StartView } | null = null;
  /** Where this client boots: the farm schedules its tile and ring as the player's until placement. */
  private bootAt: { x: number; z: number } | null = null;
  /** Preload actual starting ground; distant draft expansion joins ordinary streaming after placement.
   *  `boot` marks the position the client actually starts at (not every menu choice). */
  preload(points: readonly { x: number; z: number }[], radius: number, boot = false, view?: StartView): () => void {
    this.startup = { points, radius, view };
    if (boot) this.bootAt = points[0] ?? null;
    return () => { this.startup = null; this.nextCheck = 0; this.bootAt = null; };
  }
  /** Distance the tile farm schedules by: while the client boots, its boot position counts as the
   *  player, so that tile and its ring build first. */
  private demandDistance(id: string, x: number, z: number) {
    const boot = this.startup ? this.bootAt : null;  // Only until the client is placed.
    return boot ? Math.min(distToL0(id, x, z), distToL0(id, boot.x, boot.z)) : distToL0(id, x, z);
  }
  private wantedStarts(x: number, z: number) {
    if (this.startup) {
      const ground = startupDraftTiles(this.startup.points, this.startup.radius), view = this.startup.view;
      if (!view) return ground;
      const length = Math.hypot(view.dx, view.dz) || 1;
      return [...new Set([...ground, ...viewWedgeTiles(view.x, view.z, view.dx / length, view.dz / length, START_VIEW_M, START_VIEW_HALF)])];
    }
    return [...new Set([{ x, z }, ...this.retainedStarts].flatMap(p => wantedTiles(p.x, p.z, this.host.detailRadius)))];
  }
  private async prepareModel(id: string, entry: Entry, model: THREE.Object3D, vegetation?: CoarseFragment['vegetation']) {
    const state = entry.state;
    let ready = false;
    try {
      let stage = streamTrace.begin('tile.style', id);
      await this.host.style?.(model); stage();
      if (this.entries.get(id) !== entry || entry.state !== state || entry.abandoned) return false;
      stage = streamTrace.begin('tile.facades', id);
      await prepareFacadeModules(model); stage();
      if (this.entries.get(id) !== entry || entry.state !== state || entry.abandoned) return false;
      streamTrace.time('install', () => tintDraft(model, this.host.renderer, this.materials));
      stage = streamTrace.begin('tile.vegetation', id);
      if (vegetation) await this.host.prepareVegetation?.(model, vegetation);
      stage();
      if (entry.abandoned) return false;
      // Static batches, shadow batches, vegetation draws and shader/pipeline preparation of the whole model.
      stage = streamTrace.begin('tile.prepare', id);
      await this.host.styled?.(model); stage();
      ready = this.entries.get(id) === entry && entry.state === state && !entry.abandoned;
      return ready;
    } finally {
      if (!ready && entry.abandoned) streamTrace.event('tile.abandon', id);
      if (!ready) this.dispose(model);
    }
  }

  private cacheFull(id: string, e: Entry) {
    if (this.host.detailRadius !== undefined || !e.model || e.preview || e.pass !== 'full' || !e.visualHash) return false;
    if (e.cachedAt !== undefined) return true;
    e.cachedAt = this.clock;
    e.cachedBytes = ownedGeometryBytes(e.model);
    streamTrace.event('tile.cache', id, { mib: Math.round(e.cachedBytes / 2 ** 16) / 16 });
    const model = e.model, shown = model.visible && !!model.parent;
    this.comparison.clear(e);
    model.visible = shown;
    this.holdVisual(id, model, () => {
      if (e.model !== model || e.cachedAt === undefined) return;
      model.visible = false;
      model.removeFromParent();
    });
    return true;
  }

  /** Keep a leaving full visual drawn until its scenery cell has dissolved in over it, then `finish`. */
  private holdVisual(id: string, model: THREE.Object3D, finish: () => void) {
    if (!this.screenshotScenery.radius || !model.parent || !model.visible) { finish(); return; }
    this.leaving.set(model, { id, since: this.clock, finish });
    this.screenshotScenery.holdDetail(id);
    streamTrace.event('tile.hold', id);
  }

  /** Run after the scenery update, which decides whether the cell is wanted and drawn. */
  private settleLeaving() {
    if (!this.leaving.size) return; // every frame: no copy while nothing is leaving
    for (const [model, leaving] of [...this.leaving]) {
      if (!this.screenshotScenery.mayRelease(leaving.id) && (tileDistance(leaving.id, this.at.x, this.at.z) ?? 0) < HOLD_FAR_M) continue;
      this.endLeaving(model, true);
    }
  }

  private endLeaving(model: THREE.Object3D, finish: boolean) {
    const leaving = this.leaving.get(model);
    if (!leaving) return;
    this.leaving.delete(model);
    if (![...this.leaving.values()].some(other => other.id === leaving.id)) this.screenshotScenery.releaseDetail(leaving.id);
    streamTrace.event('tile.release', leaving.id, { ms: this.clock - leaving.since, finished: finish,
      covered: finish && this.screenshotScenery.mayRelease(leaving.id) });
    if (finish) leaving.finish();
  }

  /** A new visual for the square ends any older one still drawing there. */
  private finishLeaving(id: string) {
    for (const [model, leaving] of [...this.leaving]) if (leaving.id === id) this.endLeaving(model, true);
  }

  /** Streaming queue state for trace events (only computed while a capture records). */
  private depth() {
    let pending = 0, loading = 0;
    for (const e of this.entries.values()) { if (e.state === 'pending') pending++; else if (e.state === 'loading') loading++; }
    return { pending, loading, preparing: this.preparing.size, details: this.details, leaving: this.leaving.size };
  }

  /** The lowest content height (engine y) of the resident tile under (x, z), or NaN when none is known. */
  groundBelow(x: number, z: number) {
    const h = this.entries.get(`t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`)?.h;
    return h ? h[0] : NaN;
  }

  /** Which band draws each 250 m cell of a (2r+1)^2 grid around (x, z), one character per cell, rows by z:
   *  F full HD, h held HD (leaving or going distant), P collision preview, C scenery cell, c cell dissolving,
   *  U a coarser scenery node, D double (HD and a settled cell both drawn), . nothing (a hole). */
  coverageSnapshot(x: number, z: number, r = 4) {
    const ci = Math.floor(x / VISUAL_L0), cj = Math.floor(z / VISUAL_L0);
    const leaving = new Set<string>();
    for (const l of this.leaving.values()) leaving.add(l.id);
    let cells = '';
    for (let j = cj - r; j <= cj + r; j++) for (let i = ci - r; i <= ci + r; i++) {
      const id = `t0_${i}_${j}`, e = this.entries.get(id), model = e?.model;
      const hd = !!model && !e!.preview && model.visible && !!model.parent;
      const held = leaving.has(id);
      const cell = this.screenshotScenery.cellState(id);
      cells += hd && cell === 'C' ? 'D' : hd ? (held ? 'h' : 'F') : held ? 'h'
        : e?.preview && model?.visible ? 'P' : cell ?? '.';
    }
    return { i0: ci - r, j0: cj - r, n: 2 * r + 1, cells };
  }

  private shown(e: Entry) {
    return e.cachedAt === undefined && !e.distant && (this.visualPass === 'auto' || e.pass === this.visualPass);
  }

  private restoreCached(x: number, z: number) {
    // The lead ahead too: a cached full tile the heading reaches again draws (or hands over to its scenery cell when
    // far) instead of expiring hidden and loading again.
    for (const id of [...this.wantedStarts(x,z), ...this.lead]) {
      const e = this.entries.get(id);
      if (!e?.model || e.cachedAt === undefined) continue;
      this.endLeaving(e.model, false);
      e.cachedAt = undefined;
      streamTrace.event('tile.restore', id);
      e.model.visible = this.shown(e);
      const own = this.screenshotScenery.cellState(id);
      this.screenshotScenery.nearbyChanged(id);
      this.host.scene.add(e.model);
      this.dissolveIn(id, e.model, own);
    }
  }

  /** A full model whose square shows no cell of its own (a collision preview, a coarser 1 km node or nothing draws
   *  there) dissolves in over it, like every other band; where its own cell draws, the cell dissolves out over the
   *  model instead (ScreenshotScenery.handOver). A visible preview keeps drawing underneath until the model lands.
   *  The twins were compiled with the model's preparation; without them the model shows at once (band.swap). */
  private dissolveIn(id: string, model: THREE.Object3D, own: string | undefined, under?: THREE.Object3D) {
    if (own === 'C' || own === 'c' || !model.visible || !model.parent) { if (under) this.dispose(under, id); return; }
    lodFades.cancelWithin(model);
    const started = lodFades.start(model, 0, 1, () => streamTrace.event('band.fadeEnd', id, { dir: 'in', band: 'full' }),
      this.clock, LOD_FADE.seconds, 0);
    streamTrace.event(started ? 'band.fade' : 'band.swap', id, { dir: 'in', band: 'full',
      over: under ? 'preview' : own === 'U' ? 'coarser' : 'nothing' });
    if (started && under) this.underFade.set(model, { id, under });
    else if (under) this.dispose(under, id);
  }

  /** GTA-style height-aware detail: a resident full tile far from the camera in 3D (a high flier) draws as its
   *  prepared scenery cell, which dissolves in over it; collision and residency are unchanged. */
  private updateDistant(x: number, z: number) {
    const y = this.viewY;
    for (const id of this.entries.keys()) {
      const e = this.entries.get(id)!;
      if (!e.model || e.preview || e.cachedAt !== undefined) continue;
      const dy = Number.isFinite(y) && e.h ? Math.max(0, y - e.h[1], e.h[0] - y) : 0;
      const d = Math.hypot(distToL0(id, x, z), dy);
      const near = d <= TILE_DETAIL.distance + (e.distant ? -TILE_DETAIL.band : TILE_DETAIL.band);
      if (near === !e.distant) continue;
      const model = e.model;
      if (near) {
        streamTrace.event('band.tile', id, { to: 'full', d3: Math.round(d) });
        e.distant = false;
        this.endLeaving(model, false);
        model.visible = this.shown(e);
        this.screenshotScenery.nearbyChanged(id);
      } else if (this.screenshotScenery.canTakeOver(id)) {
        streamTrace.event('band.tile', id, { to: 'cell', d3: Math.round(d) });
        e.distant = true;
        this.screenshotScenery.nearbyChanged(id);
        this.holdVisual(id, model, () => { if (e.model === model && e.distant) model.visible = false; });
      }
    }
  }

  /** Call every frame with the player position; scan at least four times per crossed tile (`now` in ms). */
  update(x: number, z: number, now = performance.now(), motion?: { x: number; z: number }, scenery = true, y = NaN) {
    this.clock = now;
    this.at.x = x; this.at.z = z;
    this.viewY = y;
    lodFades.update(now);
    if (this.underFade.size) for (const [model, { id, under }] of this.underFade)
      if (!lodFades.has(model)) { this.underFade.delete(model); this.dispose(under, id); }
    // A reversal must restore the already uploaded/compiled visual before even a throttled world scan.
    this.restoreCached(x,z);
    if (scenery) this.updateDistant(x, z);
    for (const entry of this.entries.values()) {
      // A tile dissolving in as a whole keeps its block bands until it lands (no nested block dissolves).
      if (entry.model && !lodFades.has(entry.model)) updateBuildingDistances(entry.model, x, z, undefined, undefined, y);
      if (entry.comparison) updateBuildingDistances(entry.comparison, x, z, undefined, undefined, y);
    }
    // Screenshot downloads refill free slots each frame, independently of world-generation polling.
    if (scenery) this.screenshotScenery.update(x, z, now);
    this.settleLeaving();
    if (motion && Number.isFinite(motion.x) && Number.isFinite(motion.z)) {
      if (Math.hypot(motion.x - this.velocity.x, motion.z - this.velocity.z) > 20) this.nextCheck = now;
      this.velocity = { x: motion.x, z: motion.z };
    }
    if (!this.scheduling || now < this.nextCheck) return;
    this.nextCheck = now + Math.min(POLL_MS, VISUAL_L0 / Math.max(1, Math.hypot(this.velocity.x, this.velocity.z)) * 250);
    if (!motion) this.track(x, z, now);
    const byArrival = (a: string, b: string) => arrivalToL0(a, x, z, this.velocity.x, this.velocity.z)
      - arrivalToL0(b, x, z, this.velocity.x, this.velocity.z) || distToL0(a, x, z) - distToL0(b, x, z);
    const wanted = this.wantedStarts(x, z).sort(byArrival);
    const route = this.routeHint && routeAheadTiles(this.routeHint, x, z, Math.hypot(this.velocity.x, this.velocity.z));
    const ahead = (route?.tiles ?? aheadTiles(x, z, this.velocity.x, this.velocity.z)).filter(id => !wanted.includes(id)).sort(byArrival);
    const lookahead = this.startup ? [] : preparationTiles(wanted, ahead, x, z, this.velocity.x, this.velocity.z, route?.urgent);
    const path = this.nextWindow(x, z, wanted), view = this.viewTiles(wanted);
    for (const id of view) if (!this.leadInfo.has(id)) this.leadInfo.set(id, { residence: Infinity, continuing: true });
    const nextWindow = this.lead = [...new Set([...view, ...path])];
    for (const id of [...this.leadSince.keys()]) if (!this.leadInfo.has(id)) this.leadSince.delete(id);
    for (const id of nextWindow) if (!this.leadSince.has(id)) this.leadSince.set(id, now);
    // Streaming may spend more of the frame's headroom while the current window still waits for full detail.
    streamingAllowance.urgent = wanted.some(id => { const e = this.entries.get(id); return !!e?.preview || (!!e?.refreshing && !!e.model); });
    // An install for a tile that stayed off the window, the path and the view stops at its next stage.
    for (const [id, e] of this.entries) {
      if (!e.refreshing || !e.preview || wanted.includes(id) || nextWindow.includes(id)) { this.offPath.delete(id); continue; }
      const left = this.offPath.get(id) ?? now;
      this.offPath.set(id, left);
      if (now - left >= ABANDON_AFTER_MS) e.abandoned = true;
    }
    for (const id of [...this.offPath.keys()]) if (!this.entries.get(id)?.refreshing) this.offPath.delete(id);
    for (const [id, until] of this.leadUntil) if (until < now) this.leadUntil.delete(id);
    for (const id of nextWindow) this.leadUntil.set(id, now + LEAD_KEEP_MS);
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
      if (wanted.includes(id) || lookahead.includes(id) || nextWindow.includes(id) || (e.resident && retain(id)) || (!e.resident && ahead.includes(id))) continue;
      // A prepared surface reserve tile stays while it is within the 5x5 reserve plus hysteresis, so ranking jitter
      // and boundary crossings do not drop and rebuild it.
      if (e.resident && e.preview && this.host.detailRadius === undefined && retainPreparedTile(id, [{ x, z }, ...this.retainedStarts])) continue;
      if (e.resident && (e.preview || !e.model) && (this.leadUntil.get(id) ?? 0) >= now) continue;
      if (e.state === 'built' && now < (this.builtUntil.get(id) ?? 0)) continue;
      this.builtUntil.delete(id);
      if (!this.overGpu && this.cacheFull(id, e)) continue;
      this.drop(e); this.entries.delete(id);
    }
    let anyCached = false;
    for (const e of this.entries.values()) if (e.cachedAt !== undefined) { anyCached = true; break; }
    if (anyCached) {
      const cached = [...this.entries].filter(([,e]) => e.cachedAt !== undefined)
        .sort((a,b) => b[1].cachedAt! - a[1].cachedAt!);
      let bytes = 0;
      for (let i=0;i<cached.length;i++) {
        const [id,e] = cached[i];
        bytes += e.cachedBytes ?? 0;
        // The newest cached tile always stays (a U-turn); older ones within the count, age and heap budget.
        if (i < RETURN_CACHE_TILES && now-e.cachedAt! <= RETURN_CACHE_MS && (i === 0 || bytes <= RETURN_CACHE_BYTES)) continue;
        this.drop(e); this.entries.delete(id);
      }
    }
    if (this.outage) {
      if (now >= this.outage.nextProbe && !this.outage.probing) void this.probe();
      this.show(this.remote ? 'tiles: remote service offline' : RESTARTING, true);
      return;
    }
    for (const [id, e] of this.entries) void this.compare(id, e);
    this.releaseUploadedArrays(now);
    this.lastWanted = wanted;
    this.measureGpu(now);
    void this.want(x, z, now);
    if (this.remote) {
      const demand = draftDemand([...wanted, ...lookahead], ahead, x, z, this.velocity.x, this.velocity.z, route?.urgent);
      if (this.screenshotScenery.radius) demand.tiles = [...new Set([...demand.tiles, ...this.screenshotScenery.demand()])].slice(0, 128);
      void this.sendDemand(demand);
    }
    const due = (e: Entry) => (e.state === 'pending' || e.state === 'failed' || e.state === 'built')
      && (now >= e.nextPoll || (!e.retries && e.state !== 'built' && (e.version ?? this.version) < this.version));
    for (const id of wanted) {
      const e = this.entry(id);
      if (due(e)) void this.poll(id, e, this.demandDistance(id, x, z), true);
      else if (e.state === 'draft' && !e.preview && !e.refreshing && !this.final(e) && (now >= e.nextPoll
        || (!e.retries && (e.pass !== 'full' || e.stale) && (e.version ?? -1) < this.version)))
        void this.refreshDraft(id, e, this.demandDistance(id, x, z));
    }
    // Core requests always start first. This separate slot limits speculative preparation only.
    for (const id of new Set([...nextWindow, ...lookahead])) {
      if (this.preparing.size >= PREVIEW_SLOTS || this.preparing.has(id)) continue;
      const e = this.entry(id), distance = this.demandDistance(id, x, z);
      const task = due(e) ? this.poll(id, e, distance, true, 'lookahead') : null;
      if (task) {
        this.preparing.add(id);
        void task.finally(() => this.preparing.delete(id));
      }
    }
    // Detailed facades/textures cannot monopolize the network while ground is still arriving.
    // The current window strictly first; then the windows ahead in arrival order, leaving one slot for the current one.
    for (const id of [...wanted, ...nextWindow]) {
      const e = this.entries.get(id);
      const inWindow = wanted.includes(id), slots = inWindow ? DETAIL_SLOTS : AHEAD_DETAIL_SLOTS;
      if (!inWindow && (!this.leadReady(id, now) || this.overGpu)) continue;
      if (e?.preview && !this.withinHdBand(id, e.preview, x, z)) continue;
      if (e?.preview && !e.refreshing && this.details < slots && now >= e.nextPoll) {
        e.refreshing = true; e.abandoned = false; this.details++;
        void this.applyFragment(id, e, e.preview).catch(error => this.warn(`[coarse] ${id} detail: ${error}`))
          .finally(() => { e.refreshing = false; e.abandoned = false; e.nextPoll = this.clock + 2000; this.details--; });
      }
    }
    // All other ahead tiles request builds without loading model or collision resources.
    // Nearest arrival first, a few at a time: a burst of distant acknowledgements must not delay near tiles.
    for (const id of ahead) {
      if (lookahead.includes(id) || nextWindow.includes(id)) continue;
      const e = this.entry(id);
      if (!due(e) || e.state === 'built') continue;
      if (this.cachedFragment(id)) { void this.poll(id, e, this.demandDistance(id, x, z), false, 'ahead'); continue; }
      if (this.acking >= ACK_SLOTS) continue;
      this.acking++;
      void this.poll(id, e, this.demandDistance(id, x, z), false, 'ahead').finally(() => this.acking--);
    }
    const here = `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
    const building = this.remote ? [...this.entries.values()].filter(e => e.state === 'pending' || e.state === 'loading').length : this.building;
    const queueFull = this.remote && [...this.entries.values()].some(e => e.error === 'queue full');
    // ('draft' is the internal name of a loaded tile.) Counts only; the ids are on demand (loadedTiles, __tiles).
    const loaded = [...this.entries.values()].filter(e => e.state === 'draft').length;
    const installing = [...this.entries.values()].filter(e => e.refreshing && e.preview).length;
    const at = this.entries.get(here);
    const inSeconds = (e?: Entry) => e?.readyAt === undefined ? null : Math.max(0, Math.ceil((e.readyAt - now) / 1000));
    const ready = inSeconds(at);
    const waiting = at?.state === 'pending' && at.error ? ` ${at.error}` : ready !== null ? ` ready in ${ready} s` : ' building...';
    // Something the player would notice: the tile underfoot or one of its window is not loaded yet, or failing.
    // A window tile counts once it has waited WAITING_MS: every crossing briefly waits for the new row.
    const missing = wanted.filter(id => {
      const e = this.entries.get(id);
      return (e?.state === 'pending' || e?.state === 'loading') && now - (e.since ?? now) > WAITING_MS;
    });
    const failing = wanted.find(id => this.entries.get(id)?.state === 'failed');
    const head = !at ? '' : at.state === 'draft' ? `tile ${here}`
      : `tile ${here}${at.state === 'failed' ? ` retrying: ${at.error}` : waiting}`;
    const problems = [
      failing && failing !== here ? `retrying ${failing}: ${this.entries.get(failing)!.error}` : '',
      missing.length && at?.state === 'draft' ? `waiting for ${missing.length === 1 ? missing[0] : `${missing.length} tiles`}` : '',
      queueFull ? 'remote queue full; retrying' : '',
    ].filter(Boolean);
    this.show([head, loaded ? `${loaded} loaded` : '', installing ? `${installing} installing` : '',
      building ? `${building} building` : '', ...problems].filter(Boolean).join(' · '), (!!at && at.state !== 'draft') || problems.length > 0);
  }

  /** Startup waits for the requested draft ground on both localhost and Pages. A draft whose build failed, or that the
   *  service is still building after START_BUILD_GRACE_MS, blocks startup only under a start point: it is left out
   *  and warned about once, and the world starts without that tile (it streams in when it is built). */
  progressAt(x: number, z: number) {
    const points = [{ x, z }, ...this.retainedStarts, ...this.startup?.points ?? []];
    const critical = new Set(startupDraftTiles(points, 3));
    const wanted = this.wantedStarts(x, z).filter(id => {
      const entry = this.entries.get(id);
      if (!entry || critical.has(id)) return true;
      const building = entry.state !== 'draft' && entry.building !== undefined && this.clock - entry.building > START_BUILD_GRACE_MS;
      if (entry.state !== 'failed' && !building) return true;
      if (!this.skipped.has(id)) {
        this.skipped.add(id);
        this.warn(building ? `draft ${id} is still building, starting without it` : `draft ${id} failed, starting without it: ${entry.error}`);
      }
      return false;
    });
    const ready = wanted.filter(id => this.entries.get(id)?.state === 'draft');
    return { loaded: ready.length, total: wanted.length, errors: [] as string[] };
  }

  /** Whether the L0 tile under (x, z) is settled: drawn, or failed for now. Until then its collision cells are not
   *  in the manifest, so "no collision entry" there does not mean open space. */
  resolvedAt(x: number, z: number) {
    const state = this.entries.get(`t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`)?.state;
    return state === 'draft' || state === 'failed';
  }
  /** True when every drawn tile around (x, z) carries its full visual model, not a collision preview. Play may
   *  start on previews; fixed-camera QA and the bench probe wait for the real surfaces. */
  detailedAt(x: number, z: number) {
    const window = this.wantedStarts(x, z).every(id => { const e = this.entries.get(id); return e?.state !== 'draft' || !e.preview; });
    // Settling cameras also wait for the view wedge within START_VIEW_M (tiles the HD band admits), so a bench or QA
    // view never starts on scenery cells there.
    const view = this.startup ? null : this.screenshotScenery.cameraView;
    // Over the GPU budget no view tile beyond the window installs: waiting for them would never end.
    if (!window || !view || this.overGpu) return window;
    const wanted = this.wantedStarts(x, z);
    return viewWedgeTiles(view.x, view.z, view.dx, view.dz, START_VIEW_M, Math.min(view.halfAngle, Math.PI)).every(id => {
      if (wanted.includes(id)) return true;
      const e = this.entries.get(id);
      if (e?.state === 'failed') return true;
      if (e?.preview && !this.withinHdBand(id, e.preview, x, z)) return true;
      return e?.state === 'draft' && !e.preview && !!e.model;
    });
  }
  /** Strict render coverage for tools and diagnostics; unlike startup, failed tiles are never skipped. */
  viewStatus(x: number, z: number) {
    const near = this.wantedStarts(x, z).map(id => {
      const e = this.entries.get(id);
      return { id, hash: e?.visualHash, ready: !!e?.model && !e.preview && e.pass === 'full' && !e.stale };
    });
    const scenery = this.screenshotScenery.progress;
    return { ready: near.every(e => e.ready) && scenery.ready, near, scenery };
  }
  private skipped = new Set<string>();

  /** The dev panel and loading screen read `status`; the top badge shows only while something waits or fails. */
  private show(status: string, alert = false) {
    this.status = status;
    if (this.badge) { this.badge.textContent = alert ? status : ''; this.badge.style.display = alert && status ? '' : 'none'; }
  }

  /** Ids of the loaded tiles (on demand: `__tiles.loaded()`), sorted. */
  loadedTiles() { return [...this.entries].filter(([, e]) => e.state === 'draft').map(([id]) => id).sort(); }

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
    const message = this.remote ? 'tiles: remote service offline' : RESTARTING;
    this.warn(`[coarse] ${message}: tiles paused until it answers again`);
    this.show(message, true);
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
    if (!e) { this.entries.set(id, e = { state: 'pending', collision: new Map(), nextPoll: 0, since: this.clock }); streamTrace.event('tile.new', id); }
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

  /** Tiles the 3x3 window of a driving player reaches along its heading within the detail lead, minus the current
   *  window, in the order the window reaches them (nearest arrival first). Also records each one's predicted window
   *  residence along that path (`leadInfo`). */
  private nextWindow(x: number, z: number, wanted: string[]) {
    this.leadInfo.clear();
    const speed = Math.hypot(this.velocity.x, this.velocity.z);
    if (this.startup || this.host.detailRadius !== undefined || speed < 3 || speed > DETAIL_LEAD_MAX_SPEED) return [];
    const reach = Math.min(Math.max(DETAIL_LEAD_S * speed, DETAIL_LEAD_M), DETAIL_LEAD_MAX_M), step = VISUAL_L0 / 5;
    const seen = new Set(wanted), order: string[] = [], first = new Map<string, number>(), last = new Map<string, number>();
    let end = 0;
    for (let s = step; s < reach + step; s += step) {
      const at = Math.min(s, reach), px = x + this.velocity.x / speed * at, pz = z + this.velocity.z / speed * at;
      end = at;
      for (const id of wantedTiles(px, pz)) {
        if (!seen.has(id)) { seen.add(id); order.push(id); first.set(id, at); }
        if (first.has(id)) last.set(id, at);
      }
    }
    for (const id of order) this.leadInfo.set(id, { residence: (last.get(id)! - first.get(id)! + step) / speed, continuing: last.get(id) === end });
    return order;
  }

  /** L0 tiles outside the current window inside the camera's horizontal view wedge within VIEW_DETAIL_M, seen from
   *  the camera now and from where a driving camera will be VIEW_LEAD_S ahead (a full install takes several seconds,
   *  so it starts before the tile comes within range), nearest first. Tiles already chosen stay until they are
   *  VIEW_KEEP_M farther or a little outside the wedge: no install, cache and reinstall on the 400 m line. */
  private viewTiles(wanted: string[]) {
    const view = this.screenshotScenery.cameraView;
    if (!view || this.startup || this.host.detailRadius !== undefined) { this.viewSet.clear(); return []; }
    const { x, z, dx, dz, halfAngle } = view;
    const speed = Math.hypot(this.velocity.x, this.velocity.z);
    const ahead = speed >= 3 && speed <= DETAIL_LEAD_MAX_SPEED ? Math.min(VIEW_LEAD_S * speed, VIEW_LEAD_MAX_M) : 0;
    const origins: [number, number][] = [[x, z]];
    if (ahead) origins.push([x + this.velocity.x / speed * ahead, z + this.velocity.z / speed * ahead]);
    const inWedge = (i: number, j: number, ox: number, oz: number, range: number, angle: number) =>
      inViewWedge(i, j, ox, oz, dx, dz, range, angle);
    const reach = VIEW_DETAIL_M + VIEW_KEEP_M + ahead, out: { id: string; d: number }[] = [];
    for (let i = Math.floor((x - reach) / VISUAL_L0); i <= Math.floor((x + reach) / VISUAL_L0); i++) {
      for (let j = Math.floor((z - reach) / VISUAL_L0); j <= Math.floor((z + reach) / VISUAL_L0); j++) {
        const id = `t0_${i}_${j}`;
        if (wanted.includes(id)) continue;
        const chosen = origins.some(([ox, oz]) => inWedge(i, j, ox, oz, VIEW_DETAIL_M, halfAngle))
          || (this.viewSet.has(id) && inWedge(i, j, x, z, VIEW_DETAIL_M + VIEW_KEEP_M, halfAngle + 0.15));
        if (chosen) out.push({ id, d: distToL0(id, x, z) });
      }
    }
    const ordered = out.sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1)).map(t => t.id);
    this.viewSet = new Set(ordered);
    return ordered;
  }

  /** A full install is due on the ground, or for a high camera within HD_3D_M of 3D distance to the tile. */
  private withinHdBand(id: string, fragment: CoarseFragment, x: number, z: number) {
    if (!Number.isFinite(this.viewY)) return true;
    const top = (fragment.visual[id]?.h as [number, number] | undefined)?.[1] ?? 0;
    const above = this.viewY - top;
    return above < HIGH_CAMERA_M || Math.hypot(distToL0(id, x, z), above) <= HD_3D_M;
  }

  /** A lead tile is worth a full install: covered for LEAD_DWELL_MS and held by the window long enough. */
  private leadReady(id: string, now: number) {
    const info = this.leadInfo.get(id), since = this.leadSince.get(id);
    return !!info && since !== undefined && now - since >= (this.viewSet.has(id) ? VIEW_DWELL_MS : LEAD_DWELL_MS)
      && (info.continuing || info.residence >= LEAD_MIN_RESIDENCE_S);
  }

  /** A full, non-stale fragment of the service's current generation never changes: no refresh polls or refetches. */
  private final(f: { generation?: string; stale?: boolean; pass?: string; pass_kind?: string }) {
    return this.generation !== undefined && f.generation === this.generation && f.stale !== true && (f.pass ?? f.pass_kind) === 'full';
  }

  /** A cached final fragment of the current generation, most recently used last. */
  private cachedFragment(id: string) {
    const cached = this.fragments.get(id);
    if (!cached || this.generation === undefined || cached.generation !== this.generation) return null;
    this.fragments.delete(id); this.fragments.set(id, cached);
    return cached;
  }

  private cacheFragment(id: string, fragment: Pick<CoarseFragment, 'generation' | 'stale' | 'pass_kind'>, hash: string, bytes: number, read: () => Promise<CoarseFragment>) {
    const old = this.fragments.get(id);
    if (old) { this.fragmentBytes -= old.bytes; this.fragments.delete(id); }
    // Kept by its own generation: the service generation may arrive after the first fragments.
    if (typeof fragment.generation !== 'string' || fragment.stale === true || fragment.pass_kind !== 'full' || bytes > FRAGMENT_CACHE_BYTES) return;
    this.fragments.set(id, { generation: fragment.generation!, hash, bytes, read });
    this.fragmentBytes += bytes;
    for (const [key, entry] of this.fragments) {
      if (this.fragmentBytes <= FRAGMENT_CACHE_BYTES) break;
      this.fragments.delete(key); this.fragmentBytes -= entry.bytes;
    }
  }

  /** Position and velocity for the service's raw-data prefetch (pipeline/turku3d/coarse/prefetch.py). One request at
   *  a time; while nothing is building or waiting, only after a real move or every WANT_IDLE_MS. */
  private async want(x: number, z: number, now: number) {
    if (this.remote || this.wantBusy) return; // Public clients poll tiles independently; no shared player position.
    const waiting = [...this.entries.values()].some(e => e.state === 'pending' || e.state === 'loading' || e.state === 'failed');
    if (!waiting && this.building === 0 && this.generation !== undefined && now < this.wantNext
      && this.wantAt && Math.hypot(x - this.wantAt.x, z - this.wantAt.z) < WANT_MOVE_M) return;
    this.wantBusy = true; this.wantNext = now + WANT_IDLE_MS; this.wantAt = { x, z };
    const q = [x, z, this.velocity.x, this.velocity.z].map(v => v.toFixed(1));
    try {
      const res = await this.request(`${this.root}want?x=${q[0]}&z=${q[1]}&vx=${q[2]}&vz=${q[3]}`);
      if (res.status === 404) return; // an older service without the prefetch route: drafts work without it
      const { version, queued, building, generation } = await res.json().catch(() => ({})) as { version?: number; queued?: number; building?: string[]; generation?: string };
      if (typeof queued === 'number' && Array.isArray(building)) this.building = queued + building.length;
      if (typeof generation === 'string' && generation !== this.generation) {
        // A new generation (a repin or migration): every resident tile revalidates at once; cached bodies are stale.
        const first = this.generation === undefined;
        this.generation = generation;
        for (const [key, cached] of this.fragments) if (cached.generation !== generation) { this.fragments.delete(key); this.fragmentBytes -= cached.bytes; }
        if (!first) { for (const e of this.entries.values()) e.nextPoll = 0; this.nextCheck = 0; }
      }
      if (typeof version !== 'number' || version === this.version) return;
      // A build finished: the next frame polls every tile last polled before it (see `due`). The first answer only
      // sets the baseline.
      if (this.version < 0) for (const e of this.entries.values()) e.version = version;
      else this.nextCheck = 0;
      this.version = version;
    } catch (err) { this.down(err instanceof ServiceDown ? err : undefined); }
    finally { this.wantBusy = false; }
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
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(d)}${this.remote || this.named ? `&client=${this.client}` : ''}`);
      if (res.status === 429) {
        e.nextPoll = this.clock + Math.max(retryAfter(res), backoff(e.retries = (e.retries ?? 0) + 1));
        return;
      }
      if (!res.ok || res.status === 202) return;
      const { fragment, hash } = await this.readFragment(res, e, id);
      if (fragment) {
        streamTrace.event('tile.refresh', id, { pass: fragment.pass_kind });
        await this.applyFragment(id, e, fragment);
        e.manifestHash = hash;
        e.unchangedPolls = 0;
      } else e.unchangedPolls = (e.unchangedPolls ?? 0) + 1;
      e.nextPoll = this.clock + Math.min(30_000, 2000 * 2 ** Math.min(e.unchangedPolls ?? 0, 4));
      e.retries = 0; this.outageFailures = 0;
    } catch (err) {
      if (err instanceof ServiceDown) this.down(err);
      else this.warn(`[coarse] ${id} facade refresh: ${(err as Error).message}`);
      e.nextPoll = this.clock + backoff(e.retries = (e.retries ?? 0) + 1);
    } finally { e.refreshing = false; }
  }

  /** Hash the response bytes before parsing: freshness-only changes still reach recordFreshness. A final fragment's
   *  bytes are kept (FRAGMENT_CACHE_BYTES) for the tile's next poll. */
  private async readFragment(res: Response, e: Entry, id?: string): Promise<{ fragment: CoarseFragment | null; hash?: string }> {
    // Service unit fixtures may expose JSON only. Production always uses the worker byte path.
    if (typeof Worker === 'undefined') {
      const fragment = await res.json() as CoarseFragment;
      const hash = JSON.stringify(fragment);
      if (id) this.cacheFragment(id, fragment, hash, 2 * hash.length, async () => JSON.parse(hash) as CoarseFragment);
      return { fragment: hash === e.manifestHash && !e.preview ? null : fragment, hash };
    }
    const buffer = await res.arrayBuffer();
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(b => b.toString(16).padStart(2, '0')).join('');
    if (hash === e.manifestHash && !e.preview) return { fragment: null, hash };
    // The worker detaches its input; the cache keeps its own copy.
    const copy = id ? buffer.slice(0) : null;
    const fragment = await parseStreamingJson<CoarseFragment>(buffer);
    if (id && copy) this.cacheFragment(id, fragment, hash, copy.byteLength, () => parseStreamingJson<CoarseFragment>(copy.slice(0)));
    return { fragment, hash };
  }

  /** An acknowledgement needs only the fragment's identity. The worker parses it and returns top-level fields (no
   *  main-thread record delivery); the bytes wait in the fragment cache for the tile's load. */
  private async peekFragment(res: Response, id: string) {
    let head: Pick<CoarseFragment, 'generation' | 'stale' | 'pass_kind'> & { coarse: boolean; tile: unknown; visual: boolean };
    if (typeof Worker === 'undefined') {
      const fragment = await res.json() as CoarseFragment, hash = JSON.stringify(fragment);
      head = { coarse: fragment.coarse === true, tile: fragment.tile, generation: fragment.generation, stale: fragment.stale,
        pass_kind: fragment.pass_kind, visual: !!fragment.visual?.[id] };
      if (head.visual) this.cacheFragment(id, head, hash, 2 * hash.length, async () => JSON.parse(hash) as CoarseFragment);
      return head;
    }
    const buffer = await res.arrayBuffer(), copy = buffer.slice(0);
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(b => b.toString(16).padStart(2, '0')).join('');
    const { scalars, keys } = await peekStreamingJson(buffer);
    head = { coarse: scalars.coarse === true, tile: scalars.tile, visual: !!keys.visual?.includes(id),
      generation: typeof scalars.generation === 'string' ? scalars.generation : undefined, stale: scalars.stale === true,
      pass_kind: scalars.pass_kind === 'first' || scalars.pass_kind === 'full' ? scalars.pass_kind : undefined };
    if (head.visual) this.cacheFragment(id, head, hash, copy.byteLength, () => parseStreamingJson<CoarseFragment>(copy.slice(0)));
    return head;
  }

  private async loadFragment(id: string, fragment: Pick<CoarseFragment, 'visual' | 'buildingPlacements'>, abandoned = () => false) {
    const base = new URL(`${this.root}${id}/`, this.page);
    // Building models read while the tile GLB loads, nearest tile first.
    const priority = Number.isFinite(this.at.x) ? distToL0(id, this.at.x, this.at.z) : Infinity;
    const prefetched = fragment.buildingPlacements ? this.buildingModels.prefetch(fragment.buildingPlacements, base, priority) : null;
    const glb = streamTrace.begin('tile.glb', id, { bytes: fragment.visual[id].bytes, tris: fragment.visual[id].tris });
    let root: THREE.Object3D;
    try { root = await this.load(new URL(fragment.visual[id].url, base).href); }
    catch (error) { prefetched?.(); throw error; }
    glb();
    root.userData.traceId = id;
    try {
      // A cancelled install does not clone its buildings; the caller sees the abandoned entry and returns.
      if (fragment.buildingPlacements && !abandoned()) {
        // Tile GLBs carry a translated root node. Placements are siblings in world-local coordinates.
        const buildings = streamTrace.begin('tile.buildings', id, { models: fragment.buildingPlacements.models.length,
          placements: fragment.buildingPlacements.placements.length, blocks: fragment.buildingPlacements.blocks?.length ?? 0 });
        const release = await this.buildingModels.attach(root, fragment.buildingPlacements, base, priority);
        buildings();
        this.buildingLeases.set(root, release);
      }
      return root;
    } catch (error) { this.dispose(root); throw error; }
    finally { prefetched?.(); }
  }

  /** Commit a validated visual/collision pair together after model preparation. */
  private async applyFragment(id: string, e: Entry, fragment: CoarseFragment) {
    const state = e.state;
    if (!fragment.coarse || fragment.tile !== id || !fragment.visual?.[id]) throw new DraftRejected(`${id}: invalid draft fragment`);
    if (e.model && !e.preview && !acceptsVisualPass(e.pass, fragment.pass_kind)) return;
    const placementsKey = JSON.stringify(fragment.buildingPlacements ?? null);
    const visualChanged = !e.model || fragment.visual[id].sha256 !== e.visualHash || placementsKey !== e.placementsKey;
    const missing = cellsOf(id).filter(cid => !fragment.collision[cid]);
    if (missing.length) throw new DraftRejected(`no collision for ${missing.length} of 16 cells (${missing[0]})`);
    const base = new URL(`${this.root}${id}/`, this.page);
    const roadsUrl = fragment.roads ? new URL(fragment.roads.url, base).href : undefined;
    const roadsKey = roadsUrl && (/\.([a-f0-9]{64})\.json(?:[?#].*)?$/.exec(roadsUrl)?.[1] ?? roadsUrl);
    const roadsChanged = roadsKey !== e.roadsKey;
    const changed = Object.entries(fragment.collision).filter(([cid, entry]) => !e.collision.has(cid) || entry.sha256 !== e.collision.get(cid)?.sha256);
    const buffers = new Map<string, ArrayBuffer>();
    const validate = async () => {
      const done = streamTrace.begin('tile.collision', id, { cells: changed.length, bundle: !!fragment.collision_bundle });
      try { await validateCells(); } finally { done(); }
    };
    const validateCells = async () => {
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
      try { if (fragment.vegetation) await this.host.prepareVegetation?.(model, fragment.vegetation); }
      catch (error) { this.dispose(model); throw error; }
      if (this.entries.get(id) !== e || e.state !== state) { this.dispose(model); return; }
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
      this.finishLeaving(id);
      e.model = model; e.preview = fragment; e.pass = fragment.pass_kind; e.state = 'draft';
      model.userData.traceId = id;
      this.screenshotScenery.nearbyChanged(id);
      recordFreshness(e, fragment);
      streamTrace.event('tile.preview', id, this.depth());
      // Collision is published at once; the preview draws once its program and buffers are prepared outside a frame
      // (a first draw would upload its several MB of vertices inside the frame).
      const show = () => {
        if (e.model !== model || this.entries.get(id) !== e) return;
        this.host.scene.add(model);
        this.arrays?.preview(model);
      };
      if (!this.host.preparePreview) { show(); return; }
      await this.host.preparePreview(model).then(show, error => { this.warn(`[coarse] ${id} preview: ${error}`); show(); });
      return;
    }
    // Fetch/decode geometry while the physics worker validates collision. Visibility still commits atomically.
    const [validation, geometry, streets] = await Promise.allSettled([
      validate(), visualChanged ? this.loadFragment(id, fragment, () => !!e.abandoned) : Promise.resolve(null),
      roadsChanged && roadsUrl ? this.request(roadsUrl)
        .then(r => { if (!r.ok) throw new DraftRejected(`roads: HTTP ${r.status}`); return readStreamingJson<RoadGraphJson>(r); })
        : Promise.resolve(e.roads ?? null),
    ]);
    const model = geometry.status === 'fulfilled' ? geometry.value : null;
    const failure = [validation, geometry, streets].find(result => result.status === 'rejected');
    if (failure?.status === 'rejected') { if (model) this.dispose(model); throw failure.reason; }
    if (this.entries.get(id) !== e || e.state !== state || e.abandoned) {
      if (model) this.dispose(model);
      if (e.abandoned) streamTrace.event('tile.abandon', id);
      return;
    }
    const roads = streets.status === 'fulfilled' ? streets.value : null;
    if (!visualChanged) {
      recordFreshness(e, fragment);
      this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
      this.takeVehicles(e, fragment, base);
      e.lights = fragment.lights ?? undefined;
      if (roadsChanged) { e.roads = roads ?? undefined; e.roadsKey = roadsKey; this.onMapChange?.(); }
      return;
    }
    if (!model) throw new DraftRejected(`${id}: missing visual model`);
    // Full models dissolve in where no cell of their own draws: their preparation compiles the twins (main.ts).
    model.userData.dissolveIn = true;
    if (!await this.prepareModel(id, e, model, fragment.vegetation)) return;
    recordFreshness(e, fragment);
    // Detach before attaching (scene hooks must never observe both passes), except a drawn collision preview, which
    // stays under the full model while that dissolves in over it.
    const old = e.model, own = this.screenshotScenery.cellState(id);
    const under = old && e.preview && old.visible && old.parent && own !== 'C' && own !== 'c' ? old : undefined;
    if (old !== under) old?.removeFromParent();
    this.take(e, changed.map(([cid, entry]) => [cid, { ...entry, url: new URL(entry.url, base).href }]), buffers);
    const installing = streamTrace.on ? performance.now() : 0, wasPreview = !!e.preview;
    if (old) this.endLeaving(old, false);
    this.finishLeaving(id);
    if (e.cachedAt === undefined) this.host.scene.add(model);
    this.comparison.clear(e); e.pass = fragment.pass_kind;
    e.h = fragment.visual[id].h as [number, number] | undefined;
    e.model = model; model.visible = this.shown(e); e.visualHash = fragment.visual[id].sha256; e.placementsKey = placementsKey;
    e.state = 'draft'; e.preview = undefined; e.lights = fragment.lights ?? undefined;
    this.screenshotScenery.nearbyChanged(id);
    this.takeVehicles(e, fragment, base);
    if (old && old !== under) this.dispose(old, id);
    if (roadsChanged) { e.roads = roads ?? undefined; e.roadsKey = roadsKey; this.onMapChange?.(); }
    this.host.trackModel?.(model, id);
    this.releaseCommitted(model);
    // A refreshed full model replaces the previous one in place; first installs and previews dissolve in.
    if (!old || wasPreview) this.dissolveIn(id, model, own, under);
    if (installing) {
      streamTrace.slice('install', performance.now() - installing);
      streamTrace.event('tile.full', id, { pass: e.pass, replaced: old ? (wasPreview ? 'preview' : 'model') : null,
        tris: fragment.visual[id].tris, distant: !!e.distant, ...this.depth() });
    }
  }

  private async poll(id: string, e: Entry, d: number, load: boolean, role = 'wanted') {
    e.resident = load;
    e.state = 'loading';
    e.version = this.version;
    const traced = streamTrace.on ? streamTrace.begin('tile.manifest', id, { role, load, demand: Math.round(d), ...this.depth() }) : null;
    try {
      const query = `?d=${Math.round(d)}${this.remote || this.named ? `&client=${this.client}` : ''}`;
      // The final fragment of this generation is already here: an acknowledgement needs no request, a load no refetch.
      const cached = this.cachedFragment(id);
      if (cached) {
        traced?.({ status: 200, cached: true });
        if (!load) { this.markBuilt(id, e); return; }
        const fragment = await cached.read();
        if (this.entries.get(id) !== e) return;
        await this.loadPolled(id, e, fragment, cached.hash);
        return;
      }
      let res = await this.request(`${this.root}${id}/collision.json${query}`);
      // Older producers remain usable while a source deployment is switching.
      if (res.status === 404) res = await this.request(`${this.root}${id}/manifest.json${query}`);
      if (res.status === 429) {
        traced?.({ status: 429 });
        e.state = 'pending'; e.error = 'queue full'; e.readyAt = undefined;
        e.nextPoll = this.clock + Math.max(retryAfter(res), backoff(e.retries = (e.retries ?? 0) + 1));
        return;
      }
      if (res.status === 202) {
        const body = await res.json().catch(() => ({})) as { state?: string; missing?: string[]; eta?: number };
        e.readyAt = typeof body.eta === 'number' ? this.clock + 1000 * body.eta : undefined;
        e.error = body.state === 'pending-data' ? `waiting for ${[...new Set(body.missing?.map(u => u.split('/')[0]))].join(', ')} data`
          : undefined;
        traced?.({ status: 202, state: body.state, eta: body.eta });
        e.building ??= this.clock;
        e.state = 'pending'; e.nextPoll = this.clock + 2000; return;
      }
      e.building = undefined;
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { error?: string };
        e.state = 'failed'; e.error = body.error ?? `HTTP ${res.status}`; e.nextPoll = this.clock + RETRY_MS;
        traced?.({ status: res.status, error: e.error });
        return;
      }
      if (!load) {
        let head = await this.peekFragment(res, id);
        // A legacy collision-only response cannot expose invisible obstacles.
        if (!head.visual) {
          res = await this.request(`${this.root}${id}/manifest.json${query}`);
          if (res.status === 202) { e.state = 'pending'; e.nextPoll = this.clock + 2000; return; }
          if (!res.ok) throw new DraftRejected(`${id}: no visible draft`);
          head = await this.peekFragment(res, id);
        }
        if (!head.coarse || head.tile !== id || !head.visual) throw new DraftRejected(`${id}: no visible draft`);
        traced?.({ status: 200, pass: head.pass_kind, peek: true });
        if (this.entries.get(id) === e) this.markBuilt(id, e);
        return;
      }
      let { fragment, hash } = await this.readFragment(res, e, id);
      if (!fragment) throw new DraftRejected(`${id}: missing initial fragment`);
      // A legacy collision-only response cannot expose invisible obstacles.
      if (!fragment.visual?.[id]) {
        res = await this.request(`${this.root}${id}/manifest.json${query}`);
        if (res.status === 202) { e.state = 'pending'; e.nextPoll = this.clock + 2000; return; }
        if (!res.ok) throw new DraftRejected(`${id}: no visible draft`);
        const full = await this.readFragment(res, e, id);
        fragment = full.fragment; hash = full.hash;
      }
      if (!fragment?.coarse || fragment.tile !== id || !fragment.visual?.[id]) throw new DraftRejected(`${id}: no visible draft`);
      traced?.({ status: 200, pass: fragment.pass_kind, tris: fragment.visual[id].tris, bytes: fragment.visual[id].bytes });
      await this.loadPolled(id, e, fragment, hash);
    } catch (err) {
      if (err instanceof ServiceDown) { e.state = 'pending'; e.nextPoll = this.clock + backoff(e.retries = (e.retries ?? 0) + 1); this.down(err); return; }
      const rejected = err instanceof DraftRejected;
      streamTrace.event('tile.fail', id, { error: String((err as Error).message ?? err) });
      // A rejected draft is retried like a failed build: the service may rebuild it with a fixed producer.
      e.state = rejected ? 'failed' : 'pending'; e.nextPoll = this.clock + Math.max(rejected ? RETRY_MS : 0, backoff(e.retries = (e.retries ?? 0) + 1));
      if (rejected) e.error = (err as Error).message;
      this.warn(`[coarse] ${id}: ${(err as Error).message}`);
    }
  }

  /** The service has the tile; the acknowledgement owns no model or collision resources. */
  private markBuilt(id: string, e: Entry) {
    e.state = 'built'; e.nextPoll = 0;
    this.builtUntil.delete(id); this.builtUntil.set(id, this.clock + BUILT_CACHE_MS);
    while (this.builtUntil.size > BUILT_CACHE_TILES) this.builtUntil.delete(this.builtUntil.keys().next().value!);
  }

  private async loadPolled(id: string, e: Entry, fragment: CoarseFragment | null, hash?: string) {
    if (!fragment?.coarse || fragment.tile !== id || !fragment.visual?.[id]) throw new DraftRejected(`${id}: no visible draft`);
    this.builtUntil.delete(id);
    await this.applyFragment(id, e, fragment);
    e.manifestHash = hash;
    e.retries = 0; this.outageFailures = 0;
    // A fresh preview may start its full install at once; a full visual revalidates on the normal schedule.
    e.nextPoll = this.clock + (e.preview ? 0 : 2000);
  }

  /** Bench lab (qa/lab): false keeps update()'s per-frame work (fades, distances, scenery, holds) but requests and drops
   *  no tiles; `benchInstall` then streams the tiles a scenario chooses. */
  scheduling = true;

  /** Bench lab: stream one tile as update() does. 'preview' polls its fragment and installs the collision preview;
   *  'full' then installs the full visual of the same fragment through the detail path. Resolves when committed. */
  async benchInstall(id: string, stage: 'preview' | 'full', x: number, z: number) {
    const e = this.entry(id);
    if (stage === 'preview' && e.state !== 'draft' && e.state !== 'loading') await this.poll(id, e, this.demandDistance(id, x, z), true);
    else if (stage === 'full' && e.preview && !e.refreshing) {
      e.refreshing = true; this.details++;
      try { await this.applyFragment(id, e, e.preview); } finally { e.refreshing = false; this.details--; }
    }
    return { state: e.state, preview: !!e.preview, full: !!e.model && !e.preview, pass: e.pass ?? null, error: e.error ?? null,
      retryInMs: e.state === 'draft' ? 0 : Math.max(0, e.nextPoll - this.clock) };
  }

  private nextRelease = 0;
  /** CPU copies of uploaded render-only geometry (world/releaseArrays.ts); none while the host keeps arrays. */
  private arrays: TileArrays | null = null;
  private drawnSets = new WeakMap<object, Set<THREE.BufferGeometry>>();
  /** A full tile committed: release its uploaded render-only arrays, and templates only committed tiles hold. */
  private releaseCommitted(model: THREE.Object3D) {
    if (!this.arrays) return;
    this.arrays.commit(model);
    // Its shared textures were uploaded at load and styled since (surface arrays took their ground maps).
    releaseModelTextureData(model, texture => textureUploaded(this.host.renderer, texture));
    this.buildingModels.markCommitted(model);
    this.buildingModels.releaseCommitted((root, owners) => this.arrays!.template(root, geometry => owners.some(owner => {
      let drawn = this.drawnSets.get(owner);
      if (!drawn) { drawn = drawnGeometries(owner as THREE.Object3D); this.drawnSets.set(owner, drawn); }
      return drawn.has(geometry);
    })));
  }
  /** Every RELEASE_MS: only targets still waiting for their upload are visited. */
  private releaseUploadedArrays(now: number) {
    if (!this.arrays || now < this.nextRelease) return;
    this.nextRelease = now + RELEASE_MS;
    const bytes = this.arrays.check();
    if (bytes) streamTrace.event('mem.release', undefined, { mib: Math.round(bytes / 2 ** 16) / 16 });
  }
  /** GPU residency (GPU_BUDGET_BYTES): the latest measurement and whether new non-window installs must wait. */
  private gpu = { bytes: 0, measuring: false, next: 0 };
  private overGpu = false;
  private lastWanted: string[] = [];
  private gpuBudget = streamingBudget();
  /** The budget of the session's tier; undefined (none) on medium and high. */
  private gpuLimit() {
    if (this.host.gpuBudget !== undefined) return this.host.gpuBudget > 0 ? this.host.gpuBudget : undefined;
    const tier = renderTier(), limit = tier ? GPU_BUDGET_BYTES[tier.tier] : undefined;
    if (limit === undefined || tier?.gpu?.vendor?.toLowerCase() !== 'apple') return limit;
    const heap = jsHeapBytes();
    if (heap === undefined) return limit;
    const total = this.host.memoryBudget ?? APPLE_MEMORY_BYTES;
    return Math.min(APPLE_GPU_MAX, Math.max(APPLE_GPU_MIN, total - heap));
  }
  private measureGpu(now: number) {
    const limit = this.gpuLimit();
    if (limit === undefined || this.gpu.measuring || now < this.gpu.next) return;
    this.gpu.measuring = true; this.gpu.next = now + GPU_MEASURE_MS;
    void (async () => {
      try {
        const meter = new GpuMeter(rendererResidency(this.host.renderer));
        let bytes = 0;
        const cached = [...this.entries.values()].flatMap(e => e.model && e.cachedAt !== undefined ? [e.model] : []);
        for (const root of [...this.host.scene.children, ...cached]) {
          bytes += meter.root(root);
          const pause = this.gpuBudget.checkpoint(); if (pause) await pause;
        }
        this.gpu.bytes = bytes;
        this.applyGpuLimit(limit);
      } finally { this.gpu.measuring = false; }
    })();
  }
  /** Over the limit: drop return-cache entries (oldest first), full tiles drawn as distant cells, full tiles off the
   *  lead, then lead and view tiles (farthest first) until the estimate fits; never the current window. */
  private applyGpuLimit(limit: number) {
    // Startup installs are an explicit, bounded set the menu waits for: the budget neither stops nor evicts them.
    if (this.startup) { this.overGpu = false; return; }
    this.overGpu = this.gpu.bytes > 0.9 * limit;
    let excess = this.gpu.bytes - limit;
    if (excess <= 0) return;
    const at = this.at, wanted = new Set(this.lastWanted), lead = new Set(this.lead), residency = rendererResidency(this.host.renderer);
    const full = [...this.entries].filter(([id, e]) => e.model && !e.preview && !wanted.has(id));
    const far = (a: [string, Entry], b: [string, Entry]) => distToL0(b[0], at.x, at.z) - distToL0(a[0], at.x, at.z);
    const order = [
      ...full.filter(([, e]) => e.cachedAt !== undefined).sort((a, b) => a[1].cachedAt! - b[1].cachedAt!),
      ...full.filter(([, e]) => e.cachedAt === undefined && e.distant).sort(far),
      ...full.filter(([id, e]) => e.cachedAt === undefined && !e.distant && !lead.has(id)).sort(far),
      ...full.filter(([id, e]) => e.cachedAt === undefined && !e.distant && lead.has(id)).sort(far),
    ];
    for (const [id, e] of order) {
      if (excess <= 0) break;
      const freed = modelGpuBytes(e.model!, residency);
      streamTrace.event('tile.evict', id, { mib: Math.round(freed / 2 ** 16) / 16, cached: e.cachedAt !== undefined });
      this.drop(e); this.entries.delete(id);
      excess -= freed; this.gpu.bytes -= freed;
    }
  }
  /** The latest GPU residency measurement (bytes; 0 where the tier has no budget) and its limit. */
  gpuResidency() {
    const apple = this.host.gpuBudget === undefined && renderTier()?.gpu?.vendor?.toLowerCase() === 'apple' && jsHeapBytes() !== undefined;
    return { bytes: this.gpu.bytes, limit: this.gpuLimit() ?? null, over: this.overGpu,
      rule: this.host.gpuBudget !== undefined ? 'url' : apple && this.gpuLimit() !== undefined ? 'apple-memory' : 'tier' };
  }

  /** CPU array bytes released so far (memory census). */
  get releasedBytes() { return this.arrays?.releasedBytes ?? 0; }

  /** Streaming residency for the memory census (world/memoryCensus.ts). */
  memoryParts() {
    const full: THREE.Object3D[] = [], cached: THREE.Object3D[] = [], previews: THREE.Object3D[] = [];
    let previewFragments = 0, roads = 0;
    for (const e of this.entries.values()) {
      if (e.preview) previewFragments++;
      if (e.roads) roads++;
      if (!e.model) continue;
      (e.cachedAt !== undefined ? cached : e.preview ? previews : full).push(e.model);
    }
    const json = (pick: (e: Entry) => unknown) => () => {
      let bytes = 0;
      for (const e of this.entries.values()) { const value = pick(e); if (value) bytes += 2 * JSON.stringify(value).length; }
      return bytes;
    };
    const residency = this.gpuResidency();
    return { full, cached, previews, previewFragments, roads, releasedBytes: this.releasedBytes, releases: this.arrays?.releases ?? 0,
      gpuResidency: { measuredMiB: Math.round(residency.bytes / 2 ** 20), limitMiB: residency.limit === null ? null : Math.round(residency.limit / 2 ** 20), over: residency.over },
      previewJsonBytes: json(e => e.preview), roadsJsonBytes: json(e => e.roads),
      fragmentCache: { bytes: this.fragmentBytes, count: this.fragments.size },
      buildingTemplates: () => this.buildingModels.templates() };
  }

  /** Draft L0 tiles currently drawn. */
  drawn(): string[] {
    return [...this.entries].filter(([, e]) => e.model).map(([id]) => id);
  }

  /** The tile manifest URL the client polls (developer object editor: an edit's generation pin). */
  manifestUrl(id: string): string { return new URL(`${this.root}${id}/manifest.json`, this.page).href; }

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
      const model = e.model, pending = this.leaving.get(model);
      if (id) streamTrace.event('tile.drop', id, { preview: !!e.preview, cached: e.cachedAt !== undefined });
      // A full visual draws on until the scenery cell covers its square; collision leaves now.
      if (pending && id) pending.finish = () => this.dispose(model, id);
      else if (id && !e.preview && e.cachedAt === undefined && !e.distant) this.holdVisual(id, model, () => this.dispose(model, id));
      else this.dispose(model, id);
      e.model = undefined;
    }
    e.distant = undefined;
    for (const [cid, entry] of e.collision) {
      // An asynchronous replacement may already own the cell.
      if (this.host.manifest.collision[cid] !== entry) continue;
      delete this.host.manifest.collision[cid];
      this.host.unloadCollision(cid);
    }
    e.collision.clear();
    e.lights = undefined;
    if (e.roads) { e.roads = undefined; this.onMapChange?.(); }
    e.roadsKey = undefined; e.manifestHash = undefined;
    if (e.state === 'draft') e.state = 'pending';
  }

  private dispose(model: THREE.Object3D, id?: string) {
    lodFades.cancelWithin(model);
    model.removeFromParent();
    this.arrays?.forget(model);
    if (id) this.host.untrackModel?.(model, id);
    const release = () => {
      this.host.unstyle?.(model);
      disposeModelResources(model, this.materials);
      this.buildingLeases.get(model)?.();
      this.buildingLeases.delete(model);
    };
    if (this.host.retire) this.host.retire(release); else release();
  }

}
