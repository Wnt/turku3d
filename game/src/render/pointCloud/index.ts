// LiDAR point-cloud overlay: the city's own survey (pistepilvi, ~80 points/m², real RGB) streamed as COPC and drawn
// over the game world, as Turku's opaskartta draws it over its 3D city. The in-game editor (dev/editor) owns the mode,
// input and HUD and drives this through `PointCloudOverlay`; `?pointcloud=1` (or a colour mode) shows it at startup.
//
// Rendering: each loaded octree node is one instanced draw of single-triangle discs with screen-space size and
// distance attenuation. The points render in their own pass, depth-tested among themselves and against the scene's
// resolved depth (with a bias, so surface-hugging points stay visible), and are mixed over the tone-mapped frame by
// the opacity (render/post.ts PostComposite): colours are the survey's display sRGB, never re-exposed or graded.
// Points use the unjittered projection, so TRAA neither shakes nor smears them.
import * as THREE from 'three/webgpu';
import { Discard, Fn, If, attribute, cameraFar, cameraNear, clamp, dot, float, int, max, modelViewMatrix, modelWorldMatrix,
  pass, perspectiveDepthToViewZ, pow, positionGeometry, screenUV, select, texture, uniform, uniformArray, varying, vec3, vec4 } from 'three/tsl';
import { isPrecompiling } from '../compileModel';
import { pointCloudSources } from './sources';
import type { QualityTier } from '../quality';
import { nodeBounds, surveyToEngine, nodeAnchor, childKeys, type CopcHeader, type CopcNode, type DecodedPoints } from './copc';
import type { WorkerRequest } from './copc.worker';
import { selectNodes, type LodNode, type LodSelection } from './lod';
import { cityOf, isHome } from '../../core/city';
import { registerPointCloudOverlay, type PointCloudEvidence as HookEvidence, type PointCloudOverlay as HookOverlay } from '../../dev/editor/pointCloudHook';

/** rgb: the 2022 true ortho's colour (summer, as opaskartta shows the cloud) where the tile has it, else the survey's;
 *  survey: the LiDAR flight's own camera colour (late October); classification, elevation, intensity: ramps. */
export type ColorMode = 'rgb' | 'classification' | 'elevation' | 'intensity' | 'survey';
const MODES: readonly ColorMode[] = ['rgb', 'classification', 'elevation', 'intensity', 'survey'];

/** Evidence near a position for edit context (engine x east, z south; heights N2000 = engine y). */
export interface PointCloudEvidence extends HookEvidence {
  count: number;
  /** Points per square metre over the disc, from the loaded levels there. */
  density: number;
  minHeightM: number;
  maxHeightM: number;
  /** Point count per LAS class ("2" ground, "5" high vegetation, "6" building, "9" water, ...). */
  classes: Record<string, number>;
  /** Deepest octree level loaded under the disc and whether every level the survey has there is loaded. */
  depth: number;
  complete: boolean;
}

export interface PointCloudPoint { x: number; y: number; z: number; distance: number; classification: number; rgb: [number, number, number]; intensity: number;
  /** Whether `rgb` is the true ortho's colour (else the survey's own). */
  ortho: boolean }

/** The editor's hook (dev/editor/pointCloudHook.ts) plus the overlay's own controls; registered once constructed. */
export interface PointCloudOverlayApi extends HookOverlay {
  setVisible(on: boolean): void;
  isVisible(): boolean;
  setOpacity(alpha: number): void;
  getOpacity(): number;
  setPointSize(px: number): void;
  getPointSize(): number;
  setColorMode(mode: ColorMode): void;
  getColorMode(): ColorMode;
  /** Hide points behind the game's geometry (default) or draw them through it. */
  setOcclusion(on: boolean): void;
  setClassVisible(classification: number, on: boolean): void;
  /** Which classes draw: 'vegetation' (default: 3-5 and wires, 14), 'all' (everything but noise 7/18) or a class list. */
  setClassFilter(filter: ClassFilter): void;
  getClassFilter(): ClassFilter;
  setPointBudget(points: number): void;
  queryNear(x: number, z: number, radiusM: number): PointCloudEvidence | null;
  nearest(x: number, y: number, z: number, radiusM: number): PointCloudPoint | null;
  snapshot(): Record<string, unknown>;
}

// This module owns the global; dev/editor/pointCloudHook.ts reads it structurally.
declare global { interface Window { __turku3dPointCloud?: PointCloudOverlayApi } }

/** Hard point budgets per tier (drawn points; 24 B each on the GPU). Desktop medium is the M1 Max target. */
export const BUDGET: Record<QualityTier, number> = { mobile: 300_000, low: 600_000, reduced: 1_000_000, medium: 1_500_000, high: 3_000_000, ultra: 5_000_000 };
export const LOD = {
  /** A node refines while its own spacing projects above this many pixels. */
  refinePx: 2.5,
  /** Below this projected spacing a node draws a prefix of its shuffled points, by screen area. */
  thinPx: 1.25,
  /** Tiles farther than this (m) are not drawn. */
  maxDistance: 1200,
  /** Loaded points kept for revisits, as a multiple of the budget. */
  cacheFactor: 2.5,
  /** Concurrent node decodes and tile-hierarchy reads. */
  inFlight: 4,
  /** New node meshes attached per frame (each uploads up to ~5 MB). */
  attachPerFrame: 2,
  /** Selection cadence (ms) unless the camera moved or turned. */
  reselectMs: 120,
} as const;

/** LAS classes: display colour and default visibility. 7 (low point) and 18 (high noise) are survey noise. */
const CLASS_COLORS: Record<number, [number, number, number]> = {
  0: [0.6, 0.6, 0.6], 1: [0.78, 0.78, 0.78], 2: [0.66, 0.5, 0.33], 3: [0.62, 0.86, 0.42], 4: [0.33, 0.7, 0.26],
  5: [0.1, 0.48, 0.16], 6: [0.92, 0.36, 0.24], 7: [1, 0, 1], 8: [1, 0.85, 0.2], 9: [0.2, 0.46, 0.96],
  10: [0.6, 0.32, 0.64], 11: [0.36, 0.36, 0.42], 12: [0.86, 0.86, 0.62], 13: [1, 0.9, 0.4], 14: [1, 0.75, 0.2],
  15: [0.85, 0.65, 0.3], 16: [0.9, 0.8, 0.5], 17: [0.95, 0.6, 0.2], 18: [1, 0, 1],
};
/** Survey noise: hidden in every preset. */
const NOISE = new Set([7, 18]);
/** Low, medium and high vegetation, and wire conductors (ASPRS 14: the derived wire layer, tools/pointcloud/build_wires.py). */
export const VEGETATION_CLASSES = [3, 4, 5, 14] as const;
export type ClassFilter = 'vegetation' | 'all' | readonly number[];

interface PcTile {
  name: string;
  url: string;
  /** Engine-frame footprint from the index: min x, z, max x, z. */
  footprint: [number, number, number, number];
  state: 'idle' | 'loading' | 'ready' | 'failed';
  header?: CopcHeader;
  root?: PcNode;
  nodes?: Map<string, PcNode>;
}

interface PcNode extends LodNode {
  tile: PcTile;
  copc: CopcNode;
  parent: PcNode | null;
  children: PcNode[];
  anchor: [number, number, number];
  state: 'idle' | 'loading' | 'decoded' | 'ready' | 'failed';
  points: DecodedPoints | null;
  mesh: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.NodeMaterial> | null;
  used: number;
}

/** A worker request without its id (Omit distributed over the union). */
type JobRequest = WorkerRequest extends infer R ? R extends WorkerRequest ? Omit<R, 'id'> : never : never;

interface IndexTile { name: string; file: string; points: number; min: number[]; max: number[] }

class WorkerPool {
  private workers: Worker[] = [];
  private next = 0;
  private id = 0;
  private waiting = new Map<number, { resolve(value: unknown): void; reject(error: Error): void }>();
  constructor(count: number) {
    for (let i = 0; i < count; i++) {
      const worker = new Worker(new URL('./copc.worker.ts', import.meta.url), { type: 'module', name: `point-cloud-${i}` });
      worker.onmessage = (event: MessageEvent<{ id: number; ok: boolean; result?: unknown; error?: string }>) => {
        const job = this.waiting.get(event.data.id);
        this.waiting.delete(event.data.id);
        if (event.data.ok) job?.resolve(event.data.result); else job?.reject(new Error(event.data.error));
      };
      this.workers.push(worker);
    }
  }
  run<T>(request: JobRequest): Promise<T> {
    const id = ++this.id;
    const worker = this.workers[this.next++ % this.workers.length];
    return new Promise<T>((resolve, reject) => {
      this.waiting.set(id, { resolve: resolve as (value: unknown) => void, reject });
      worker.postMessage({ ...request, id });
    });
  }
  dispose() { for (const w of this.workers) w.terminate(); this.waiting.clear(); }
}

/** Horner evaluation of a polynomial in t (coefficients lowest order first). */
function poly(t: THREE.Node<'float'>, c: readonly number[]): THREE.Node<'float'> {
  let acc: THREE.Node<'float'> = float(c[c.length - 1]);
  for (let i = c.length - 2; i >= 0; i--) acc = t.mul(acc).add(c[i]) as THREE.Node<'float'>;
  return acc;
}

/** Turbo colour map (Google, polynomial fit) for the elevation ramp. */
function turbo(t: THREE.Node<'float'>) {
  return clamp(vec3(
    poly(t, [0.13572138, 4.6153926, -42.66032258, 132.13108234, -152.94239396, 59.28637943]),
    poly(t, [0.09140261, 2.19418839, 4.84296658, -14.18503333, 4.27729857, 2.82956604]),
    poly(t, [0.1066733, 12.64194608, -60.58204836, 110.36276771, -89.90310912, 27.34824973])), 0, 1);
}

export class PointCloudOverlay implements PointCloudOverlayApi {
  readonly scene = new THREE.Scene();
  private readonly material: THREE.NodeMaterial;
  /** One disc: the triangle circumscribing the unit circle (one primitive per point; the fragment cuts the disc). */
  private readonly corners = new THREE.Float32BufferAttribute([0, 2, 0, -Math.sqrt(3), -1, 0, Math.sqrt(3), -1, 0], 3);
  private readonly opacity = uniform(1);
  private readonly active = uniform(0);
  private readonly minPx = uniform(2.5);
  private readonly maxPx = uniform(20);
  private readonly mode = uniform(0, 'int');
  private readonly occlude = uniform(1);
  private readonly heightRange = uniform(new THREE.Vector2(-1, 45));
  private readonly projection = uniform(new THREE.Matrix4());
  private readonly focal = uniform(1);
  private readonly viewport = uniform(new THREE.Vector2(1, 1));
  private readonly palette: ReturnType<typeof uniformArray>;
  private readonly paletteValues: THREE.Vector4[] = [];
  /** The scene's resolved depth, bound by `composite` (a placeholder keeps the material complete). */
  private readonly sceneDepth = texture(new THREE.DepthTexture(1, 1));
  /** Created on first use: a hidden overlay costs no workers and no laz-perf download. */
  private workers: WorkerPool | null = null;
  private get pool() { return this.workers ??= new WorkerPool(2); }
  private readonly tiles = new Map<string, PcTile>();
  private readonly frustum = new THREE.Frustum();
  private readonly matrix = new THREE.Matrix4();
  private readonly box = new THREE.Box3();
  private readonly size = new THREE.Vector2();
  private readonly lastPose = new THREE.Vector3(Infinity, 0, 0);
  private readonly lastDir = new THREE.Vector3();
  private readonly dir = new THREE.Vector3();
  private visible = false;
  private pointSize = 2.5;
  private colorMode: ColorMode = 'rgb';
  private budget: number;
  private indexState: 'idle' | 'loading' | 'ready' | 'failed' = 'idle';
  private indexAt = 0;
  private inFlight = 0;
  private frame = 0;
  private lastSelect = 0;
  private selection: LodSelection | null = null;
  private attachQueue: PcNode[] = [];
  private loadedPoints = 0;
  private errors: string[] = [];
  /** Classes drawn (1) per LAS class; meshes hold only these points, so filtered classes cost no GPU work. */
  private readonly classMask = new Uint8Array(32);
  private classFilter: ClassFilter = 'vegetation';

  /** Where the product answered: the dev server's same-origin /pointcloud proxy, else the COPC server directly
   *  (tools/pointcloud/serve.py sends CORS headers), e.g. on a dev server started before the proxy existed. */
  private base: string | null = null;

  constructor(tier: QualityTier, private readonly bases: readonly string[] = pointCloudSources(location.hostname)) {
    this.budget = BUDGET[tier];
    for (let c = 0; c < 32; c++) {
      const [r, g, b] = CLASS_COLORS[c] ?? [0.7, 0.7, 0.7];
      this.paletteValues.push(new THREE.Vector4(r, g, b, 1));
    }
    this.palette = uniformArray(this.paletteValues, 'vec4');
    this.applyFilter('vegetation');
    this.material = this.createMaterial();
    registerPointCloudOverlay(this);
    window.__turku3dPointCloud = this;
  }

  private createMaterial() {
    const material = new THREE.NodeMaterial();
    material.name = 'point-cloud';
    material.depthTest = true; material.depthWrite = true; material.transparent = false;
    material.side = THREE.DoubleSide; material.fog = false; material.lights = false;
    const local = attribute('pcPosition', 'vec3'), color = attribute('pcColor', 'vec4'), extra = attribute('pcExtra', 'vec4');
    const survey = attribute('pcSurvey', 'vec4');
    // Per node: effective point spacing (m) after refinement and thinning; sizes the discs to close the surface.
    const spacing = uniform(1).onObjectUpdate(({ object }) => (object as THREE.Object3D).userData.pcSpacing as number);
    const corner = positionGeometry.xy;
    const view = modelViewMatrix.mul(vec4(local, 1));
    const depth = view.z.negate();
    // Unjittered projection: TRAA's view offset applies only inside the pipeline render.
    const clip = this.projection.mul(view);
    const classEntry = this.palette.element(int(color.w.mul(255).add(0.5))) as unknown as THREE.Node<'vec4'>;
    const pixels = clamp(spacing.mul(this.focal).div(max(depth, 0.05)), this.minPx, this.maxPx);
    const shown = classEntry.w.greaterThan(0.5).and(depth.greaterThan(cameraNear));
    const size = select(shown, pixels, float(0));
    material.vertexNode = vec4(clip.xy.add(corner.mul(size).div(this.viewport).mul(clip.w)), clip.zw);
    const height = modelWorldMatrix.mul(vec4(local, 1)).y;
    const range = this.heightRange;
    const elevation = turbo(clamp(height.sub(range.x).div(max(range.y.sub(range.x), 0.01)), 0, 1));
    const intensity = vec3(pow(extra.x, float(0.6)));
    const tint = select(this.mode.equal(0), color.rgb, select(this.mode.equal(1), classEntry.xyz,
      select(this.mode.equal(2), elevation, select(this.mode.equal(3), intensity, survey.rgb))));
    const rgb = varying(tint, 'vPcColor');
    const uv = varying(corner, 'vPcUv');
    const own = varying(depth, 'vPcDepth');
    material.fragmentNode = Fn(() => {
      If(dot(uv, uv).greaterThan(1), () => { Discard(); });
      const sceneZ = perspectiveDepthToViewZ(this.sceneDepth.sample(screenUV).x, cameraNear, cameraFar).negate();
      // Survey points on a surface sit within decimetres of it: hide only what is clearly behind the scene.
      If(this.occlude.greaterThan(0.5).and(own.greaterThan(sceneZ.add(own.mul(0.01)).add(0.3))), () => { Discard(); });
      return vec4(rgb, 1);
    })();
    return material;
  }

  /** The composite layer for render/post.ts: display-referred rgb and the opacity-weighted coverage in alpha. */
  composite(renderer: THREE.WebGPURenderer, camera: THREE.Camera, sceneDepth: THREE.Texture) {
    this.sceneDepth.value = sceneDepth;
    const layer = pass(this.scene, camera, { type: THREE.UnsignedByteType });
    const render = layer.updateBefore.bind(layer);
    const clear = new THREE.Color();
    layer.updateBefore = frame => {
      if (!this.visible || isPrecompiling(renderer)) return;
      // Coverage is the layer's alpha: clear to transparent whatever the renderer's clear colour.
      renderer.getClearColor(clear);
      const alpha = renderer.getClearAlpha();
      renderer.setClearColor(0x000000, 0);
      try { render(frame); } finally { renderer.setClearColor(clear, alpha); }
    };
    const sampled = layer.getTextureNode();
    return { color: vec4(sampled.rgb, sampled.a.mul(this.opacity).mul(this.active)), resource: layer };
  }

  // --- editor API ---
  setVisible(on: boolean) { this.visible = on; this.active.value = on ? 1 : 0; if (on) this.lastSelect = 0; }
  isVisible() { return this.visible; }
  setOpacity(alpha: number) { this.opacity.value = Math.min(1, Math.max(0, alpha)); }
  getOpacity() { return this.opacity.value; }
  /** Point size in CSS pixels (the drawing buffer's pixel ratio applies in `update`). */
  setPointSize(px: number) { this.pointSize = Math.min(32, Math.max(1, px)); }
  getPointSize() { return this.pointSize; }
  setColorMode(mode: ColorMode) {
    const i = MODES.indexOf(mode);
    if (i < 0) throw new Error(`unknown point-cloud colour mode ${mode}`);
    this.colorMode = mode; this.mode.value = i;
  }
  getColorMode() { return this.colorMode; }
  setOcclusion(on: boolean) { this.occlude.value = on ? 1 : 0; }
  setClassVisible(classification: number, on: boolean) {
    if (classification < 0 || classification >= 32) return;
    const classes = [...this.classMask.keys()].filter(c => this.classMask[c] && c !== classification);
    if (on) classes.push(classification);
    this.applyFilter(classes.sort((a, b) => a - b));
  }
  setClassFilter(filter: ClassFilter) { this.applyFilter(filter); }
  getClassFilter() { return this.classFilter; }

  /** New mask: the palette hides at once, and every attached node re-attaches with only the drawn classes. */
  private applyFilter(filter: ClassFilter) {
    // Presets never draw survey noise; an explicit list is drawn as given.
    const classes = filter === 'vegetation' ? VEGETATION_CLASSES : filter === 'all' ? [...Array(32).keys()].filter(c => !NOISE.has(c)) : filter;
    this.classMask.fill(0);
    for (const c of classes) if (c >= 0 && c < 32) this.classMask[c] = 1;
    this.classFilter = typeof filter === 'string' ? filter : [...filter];
    this.paletteValues.forEach((entry, c) => { entry.w = this.classMask[c]; });
    this.palette.array = this.paletteValues;
    for (const tile of this.tiles.values()) if (tile.nodes) for (const node of tile.nodes.values()) {
      if (!node.points || node.state !== 'ready') continue;
      if (node.mesh) { node.mesh.removeFromParent(); node.mesh.geometry.dispose(); node.mesh = null; }
      node.state = 'decoded'; node.loaded = false; node.drawable = undefined;
      this.attachQueue.push(node);
    }
    this.lastSelect = 0;
  }
  setPointBudget(points: number) { this.budget = Math.max(10_000, Math.round(points)); this.lastSelect = 0; }

  /** Per frame, before the post pipeline renders. */
  update(camera: THREE.PerspectiveCamera, renderer: THREE.WebGPURenderer, now = performance.now()) {
    if (!this.visible) return;
    this.frame++;
    renderer.getDrawingBufferSize(this.size);
    this.projection.value.copy(camera.projectionMatrix);
    this.viewport.value.copy(this.size);
    const focal = camera.projectionMatrix.elements[5] * this.size.y / 2;
    this.focal.value = focal;
    const ratio = this.size.y / Math.max(1, renderer.domElement.clientHeight || this.size.y);
    this.minPx.value = this.pointSize * ratio;
    this.maxPx.value = Math.min(64, this.pointSize * 8) * ratio;
    if (this.indexState === 'idle' || (this.indexState !== 'loading' && now - this.indexAt > 60_000)) void this.loadIndex(now);
    this.discoverTiles(camera.position);
    camera.getWorldDirection(this.dir);
    const moved = camera.position.distanceToSquared(this.lastPose) > 4 || this.dir.dot(this.lastDir) < 0.995;
    if (moved || now - this.lastSelect > LOD.reselectMs) {
      this.lastSelect = now; this.lastPose.copy(camera.position); this.lastDir.copy(this.dir);
      this.select(camera, focal);
    }
    this.attach();
  }

  private async loadIndex(now: number) {
    this.indexState = 'loading'; this.indexAt = now;
    const failures: string[] = [];
    for (const candidate of this.base ? [this.base] : this.bases) {
      const base = new URL(candidate, location.href).href;
      try {
        const reply = await fetch(new URL('index.json', base), { cache: 'no-store' });
        if (!reply.ok) throw new Error(`HTTP ${reply.status}`);
        // An SPA fallback answers 200 with index.html where no proxy exists.
        if (!/json/.test(reply.headers.get('content-type') ?? '')) throw new Error('not JSON');
        const index = await reply.json() as { tiles: IndexTile[] };
        this.base = base;
        this.addTiles(index.tiles, base, '');
        // The derived wire layer (class 14, tools/pointcloud/build_wires.py) is optional and grows with its build.
        const wires = await fetch(new URL('wires/index.json', base), { cache: 'no-store' }).catch(() => null);
        if (wires?.ok && /json/.test(wires.headers.get('content-type') ?? ''))
          this.addTiles((await wires.json() as { tiles: IndexTile[] }).tiles, new URL('wires/', base).href, 'wires:');
        this.indexState = 'ready';
        return;
      } catch (error) {
        failures.push(`${base}: ${(error as Error).message ?? error}`);
      }
    }
    this.indexState = 'failed';
    this.note(`index unavailable (${failures.join('; ')})`);
  }

  private addTiles(tiles: readonly IndexTile[], base: string, prefix: string) {
    for (const t of tiles) {
      const name = prefix + t.name;
      if (this.tiles.has(name)) continue;
      const a = surveyToEngine(t.min[0], t.max[1], 0), b = surveyToEngine(t.max[0], t.min[1], 0);
      this.tiles.set(name, { name, url: new URL(t.file, base).href, footprint: [a[0], a[2], b[0], b[2]], state: 'idle' });
    }
  }

  private discoverTiles(at: THREE.Vector3) {
    for (const tile of this.tiles.values()) {
      if (tile.state !== 'idle' || this.inFlight >= LOD.inFlight) continue;
      const [x0, z0, x1, z1] = tile.footprint;
      const d = Math.hypot(Math.max(x0 - at.x, 0, at.x - x1), Math.max(z0 - at.z, 0, at.z - z1));
      if (d > LOD.maxDistance) continue;
      tile.state = 'loading'; this.inFlight++;
      this.pool.run<{ header: CopcHeader; nodes: CopcNode[] }>({ kind: 'tile', url: tile.url })
        .then(({ header, nodes }) => { this.buildTree(tile, header, nodes); tile.state = 'ready'; })
        .catch(error => { tile.state = 'failed'; this.note(`${tile.name}: ${error}`); })
        .finally(() => { this.inFlight--; });
    }
  }

  private buildTree(tile: PcTile, header: CopcHeader, nodes: CopcNode[]) {
    const map = new Map<string, PcNode>();
    for (const copc of nodes) {
      const [e0, n0, h0, e1, n1, h1] = nodeBounds(header, copc);
      // Clamp the cube to the survey's height range: the octree cube is as tall as the tile is wide.
      const lo = Math.max(h0, header.min[2]), hi = Math.min(h1, header.max[2]);
      const a = surveyToEngine(e0, n1, lo), b = surveyToEngine(e1, n0, hi);
      map.set(copc.key, { key: copc.key, depth: copc.depth, pointCount: copc.pointCount, box: [a[0], a[1], a[2], b[0], Math.max(b[1], a[1]), b[2]],
        spacing: header.spacing / 2 ** copc.depth, children: [], loaded: false, tile, copc, parent: null,
        anchor: nodeAnchor(header, copc), state: 'idle', points: null, mesh: null, used: 0 });
    }
    for (const node of map.values()) for (const key of childKeys(node.copc)) {
      const child = map.get(key);
      if (child) { child.parent = node; node.children.push(child); }
    }
    tile.header = header; tile.nodes = map; tile.root = map.get('0-0-0-0');
  }

  private select(camera: THREE.PerspectiveCamera, focal: number) {
    this.matrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.matrix);
    const roots: PcNode[] = [];
    for (const tile of this.tiles.values()) if (tile.root) roots.push(tile.root);
    const selection = selectNodes(roots, {
      x: camera.position.x, y: camera.position.y, z: camera.position.z, focal,
      visible: box => this.frustum.intersectsBox(this.box.set(this.box.min.fromArray(box, 0), this.box.max.fromArray(box, 3))),
    }, { budget: this.budget, refinePx: LOD.refinePx, thinPx: LOD.thinPx, maxDistance: LOD.maxDistance });
    const previous = this.selection;
    this.selection = selection;
    const chosen = new Set(selection.selected as PcNode[]);
    if (previous) for (const node of previous.selected as PcNode[]) if (!chosen.has(node) && node.mesh) node.mesh.visible = false;
    for (const node of selection.selected as PcNode[]) {
      node.used = this.frame;
      if (node.state === 'idle' && this.inFlight < LOD.inFlight) this.load(node);
      if (!node.mesh) continue;
      const count = selection.drawn.get(node)!;
      const refined = node.children.some(c => chosen.has(c) && c.mesh);
      // Spacing of what is drawn here: halved where children add points, widened where only a prefix draws.
      // Never past the instance buffers (a larger count fails validation and the whole pass stops updating).
      const capacity = (node.mesh.geometry.getAttribute('pcPosition') as THREE.InstancedBufferAttribute).count;
      const drawn = Math.min(count, capacity);
      node.mesh.userData.pcSpacing = node.spacing / (refined ? 2 : 1) / Math.sqrt(Math.min(1, drawn / Math.max(1, capacity)));
      node.mesh.geometry.instanceCount = drawn;
      node.mesh.visible = true;
    }
    this.evict(chosen);
  }

  private load(node: PcNode) {
    node.state = 'loading'; this.inFlight++;
    this.pool.run<DecodedPoints>({ kind: 'node', url: node.tile.url, node: node.copc, header: node.tile.header!, anchor: node.anchor })
      .then(points => { node.points = points; node.state = 'decoded'; this.attachQueue.push(node); this.loadedPoints += points.count; })
      .catch(error => { node.state = 'failed'; this.note(`${node.tile.name} ${node.key}: ${error}`); })
      .finally(() => { this.inFlight--; });
  }

  private attach() {
    // Highest priority first: the queue arrives in decode-completion order.
    for (let n = 0; n < LOD.attachPerFrame && this.attachQueue.length; n++) {
      const node = this.attachQueue.shift()!;
      if (node.state !== 'decoded' || !node.points) continue;
      const kept = keepClasses(node.points, this.classMask);
      node.drawable = kept.count;
      node.weight = kept.count / Math.max(1, node.points.count);
      // Not-yet-loaded children start from their parent's share, so the budget plans with the filter.
      for (const child of node.children) if (!child.points) child.weight = node.weight;
      node.state = 'ready'; node.loaded = true;
      this.lastSelect = 0;
      if (!kept.count) continue;
      const geometry = new THREE.InstancedBufferGeometry();
      geometry.setAttribute('position', this.corners);
      geometry.setAttribute('pcPosition', new THREE.InstancedBufferAttribute(kept.positions, 3));
      geometry.setAttribute('pcColor', new THREE.InstancedBufferAttribute(kept.colors, 4, true));
      geometry.setAttribute('pcExtra', new THREE.InstancedBufferAttribute(kept.extra, 4, true));
      geometry.setAttribute('pcSurvey', new THREE.InstancedBufferAttribute(kept.survey, 4, true));
      geometry.instanceCount = 0;
      const mesh = new THREE.Mesh(geometry, this.material);
      mesh.name = `point-cloud ${node.tile.name} ${node.key}`;
      mesh.frustumCulled = false;
      mesh.position.set(...node.anchor);
      mesh.userData.pcSpacing = node.spacing;
      mesh.visible = false;
      this.scene.add(mesh);
      node.mesh = mesh;
    }
  }

  /** Drop the least recently selected loaded nodes beyond the cache budget (never a selected one). */
  private evict(chosen: Set<PcNode>) {
    const cap = this.budget * LOD.cacheFactor;
    if (this.loadedPoints <= cap) return;
    const loaded: PcNode[] = [];
    for (const tile of this.tiles.values()) if (tile.nodes) for (const node of tile.nodes.values())
      if (node.points && !chosen.has(node) && (node.state === 'ready' || node.state === 'decoded')) loaded.push(node);
    loaded.sort((a, b) => a.used - b.used || b.depth - a.depth);
    for (const node of loaded) {
      if (this.loadedPoints <= cap) break;
      this.unload(node);
    }
  }

  private unload(node: PcNode) {
    // A node's descendants refine it and draw only with it: they go too.
    for (const child of node.children) if (child.points) this.unload(child);
    if (node.mesh) { node.mesh.removeFromParent(); node.mesh.geometry.dispose(); node.mesh = null; }
    if (node.points) this.loadedPoints -= node.points.count;
    node.points = null; node.loaded = false; node.state = 'idle'; node.drawable = undefined;
  }

  private note(message: string) {
    if (this.errors.length < 20) this.errors.push(message);
    console.warn(`[point cloud] ${message}`);
  }

  // --- queries over the loaded points (the near view is the densest; far tiles answer from coarse levels) ---

  private *loadedNear(x: number, z: number, radius: number) {
    for (const tile of this.tiles.values()) {
      if (!tile.nodes) continue;
      const [x0, z0, x1, z1] = tile.footprint;
      if (x < x0 - radius || x > x1 + radius || z < z0 - radius || z > z1 + radius) continue;
      for (const node of tile.nodes.values()) {
        const b = node.box;
        if (!node.points || x < b[0] - radius || x > b[3] + radius || z < b[2] - radius || z > b[5] + radius) continue;
        yield node;
      }
    }
  }

  queryNear(x: number, z: number, radiusM: number): PointCloudEvidence | null {
    const r2 = radiusM * radiusM, classes: Record<string, number> = {};
    let count = 0, minH = Infinity, maxH = -Infinity, depth = -1, complete = true, any = false;
    for (const node of this.loadedNear(x, z, radiusM)) {
      any = true;
      const { positions: p, colors: c, count: n } = node.points!;
      const [ax, ay, az] = node.anchor;
      depth = Math.max(depth, node.depth);
      if (node.children.some(child => !child.points && boxNear(child.box, x, z, radiusM))) complete = false;
      for (let i = 0; i < n; i++) {
        const dx = p[i * 3] + ax - x, dz = p[i * 3 + 2] + az - z;
        if (dx * dx + dz * dz > r2) continue;
        const h = p[i * 3 + 1] + ay;
        count++;
        if (h < minH) minH = h;
        if (h > maxH) maxH = h;
        const k = String(c[i * 4 + 3]);
        classes[k] = (classes[k] ?? 0) + 1;
      }
    }
    if (!any) return null;
    return { count, density: count / (Math.PI * r2), minHeightM: count ? minH : NaN, maxHeightM: count ? maxH : NaN, classes, depth, complete };
  }

  nearest(x: number, y: number, z: number, radiusM: number): PointCloudPoint | null {
    let best: PointCloudPoint | null = null, bestD2 = radiusM * radiusM;
    for (const node of this.loadedNear(x, z, radiusM)) {
      const { positions: p, colors: c, extra: e, count: n } = node.points!;
      const [ax, ay, az] = node.anchor;
      for (let i = 0; i < n; i++) {
        const px = p[i * 3] + ax, py = p[i * 3 + 1] + ay, pz = p[i * 3 + 2] + az;
        const d2 = (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2;
        if (d2 >= bestD2) continue;
        bestD2 = d2;
        best = { x: px, y: py, z: pz, distance: 0, classification: c[i * 4 + 3], rgb: [c[i * 4], c[i * 4 + 1], c[i * 4 + 2]], intensity: e[i * 4] / 255,
          ortho: e[i * 4 + 3] === 1 };
      }
    }
    if (best) best.distance = Math.sqrt(bestD2);
    return best;
  }

  snapshot() {
    let ready = 0, drawn = 0;
    for (const tile of this.tiles.values()) if (tile.nodes) for (const node of tile.nodes.values())
      if (node.mesh) { ready++; if (node.mesh.visible) drawn += node.mesh.geometry.instanceCount; }
    return {
      visible: this.visible, source: this.base, classFilter: this.classFilter, opacity: this.opacity.value, pointSize: this.pointSize, colorMode: this.colorMode, budget: this.budget,
      index: this.indexState, tiles: this.tiles.size, tilesReady: [...this.tiles.values()].filter(t => t.state === 'ready').length,
      selected: this.selection?.selected.length ?? 0, selectedPoints: this.selection?.points ?? 0, drawnPoints: drawn,
      nodesReady: ready, loadedPoints: this.loadedPoints, inFlight: this.inFlight, attachQueue: this.attachQueue.length,
      errors: this.errors.slice(-5),
    };
  }

  dispose() {
    for (const tile of this.tiles.values()) if (tile.nodes) for (const node of tile.nodes.values()) this.unload(node);
    this.workers?.dispose(); this.material.dispose();
    registerPointCloudOverlay(null);
    if (window.__turku3dPointCloud === this) delete window.__turku3dPointCloud;
  }
}

/** The points of the drawn classes, in their shuffled order (so any prefix stays a uniform subsample). */
export function keepClasses(points: DecodedPoints, mask: Uint8Array) {
  const { positions, colors, survey, extra, count } = points;
  let n = 0;
  for (let i = 0; i < count; i++) if (mask[colors[i * 4 + 3]]) n++;
  if (n === count) return { count, positions, colors, survey, extra };
  const p = new Float32Array(n * 3), c = new Uint8Array(n * 4), v = new Uint8Array(n * 4), e = new Uint8Array(n * 4);
  for (let i = 0, j = 0; i < count; i++) {
    if (!mask[colors[i * 4 + 3]]) continue;
    p.set(positions.subarray(i * 3, i * 3 + 3), j * 3);
    c.set(colors.subarray(i * 4, i * 4 + 4), j * 4);
    v.set(survey.subarray(i * 4, i * 4 + 4), j * 4);
    e.set(extra.subarray(i * 4, i * 4 + 4), j * 4);
    j++;
  }
  return { count: n, positions: p, colors: c, survey: v, extra: e };
}

function boxNear(box: readonly number[], x: number, z: number, radius: number) {
  return x >= box[0] - radius && x <= box[3] + radius && z >= box[2] - radius && z <= box[5] + radius;
}

/** `?pointcloud=1` shows the overlay at startup (`=rgb|survey|classification|elevation|intensity` also picks the colours);
 *  `?pointcloud=0` leaves it out of the pipeline. Otherwise desktop play builds it hidden for the editor to show. */
export function pointCloudParam(search = location.search): { build: boolean; show: boolean; mode: ColorMode } {
  // The COPC product is Turku's survey; another city has none (core/city.ts).
  if (!isHome(cityOf(search))) return { build: false, show: false, mode: 'rgb' };
  const value = new URLSearchParams(search).get('pointcloud');
  if (value === null) return { build: true, show: false, mode: 'rgb' };
  if (value === '0' || value === 'off') return { build: false, show: false, mode: 'rgb' };
  return { build: true, show: true, mode: (MODES as readonly string[]).includes(value) ? value as ColorMode : 'rgb' };
}

