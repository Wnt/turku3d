// Visual tiles via 3DTilesRendererJS. tileset.json is in the 3D Tiles Z-up frame (docs/specs/tiles.md §1);
// the renderer rotates Y-up glTF content to Z-up, so the group gets rotation.x = -PI/2 to return to engine axes.
// `reload` swaps in a new tile release (localhost game): the new tileset loads hidden beside the current one and
// replaces it once it has settled for the player's ring (or the QA view). Listeners subscribe to `events`, which carries the models
// of both, so styling, telemetry and draft tiles follow the swap.
import * as THREE from 'three/webgpu';
import { TilesRenderer, type Tile } from '3d-tiles-renderer';
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/plugins';
import { textureUploads } from '../render/textureUploads';
import { LoadingKtx } from './loadingKtx';
import { loading } from '../core/loading';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { batchTileShadows } from './shadowBatch';
import { disposeShadowBatch } from './shadowChunks';
import { refinableParents } from './residency';
import { TileReuse } from './tileReuse';
import { PlayerLoadRegion } from './loadRegion';

interface ViewErrorTarget { error: number }
type ViewError = (tile: Tile, target: ViewErrorTarget) => void;
type ModelEvent = { scene: THREE.Object3D; tile: { extras?: Record<string, unknown> } };
interface TileEvents { 'load-model': ModelEvent; 'dispose-model': ModelEvent }

/** One tileset renderer and its L0 refinement state. */
interface Tileset {
  renderer: TilesRenderer;
  /** 500 m parents of L0 tiles -> their L0 child ids, found as the traversal reaches them. */
  l0Parents: Map<string, string[]>;
  refinable: Set<string>;
  loaded: number;
  reuse?: TileReuse;
  region?: PlayerLoadRegion;
}

export class VisualTiles {
  /** Model load and dispose events of the current tileset and of a release being swapped in. */
  readonly events = new THREE.EventDispatcher<TileEvents>();
  /** Pole residency sees both published and draft models without repeating material styling events. */
  readonly modelEvents = new THREE.EventDispatcher<TileEvents>();
  /** Models currently in the scene, including drafts registered by CoarseTiles. */
  readonly models = new Set<THREE.Object3D>();
  trackModel(scene: THREE.Object3D, id: string) {
    this.models.add(scene);
    this.modelEvents.dispatchEvent({ type: 'load-model', scene, tile: { extras: { id } } });
  }
  untrackModel(scene: THREE.Object3D, id: string) {
    this.models.delete(scene);
    this.modelEvents.dispatchEvent({ type: 'dispose-model', scene, tile: { extras: { id } } });
  }
  residentTris = 0;
  residentModels = 0;
  loadedModels = 0;
  /** Tile releases swapped in place since the page loaded. */
  swaps = 0;
  errors: string[] = [];
  private current: Tileset;
  private next: Tileset | null = null;
  /** Runs inside the tile parse promise, after raw resource ownership is recorded and before LOD replacement. */
  prepareModel?: (model: THREE.Object3D, tile: Tile, signal: AbortSignal) => Promise<void>;
  private configured = new WeakMap<THREE.Object3D, number>();
  private triCount = new Map<THREE.Object3D, number>();
  private shadowBatches = new Map<THREE.Object3D, THREE.Mesh>();
  private resolution = new THREE.Vector2();
  private ktx2: LoadingKtx;
  private swapped: (() => void) | null = null;
  private retainedStarts: readonly { x: number; z: number }[] = [];
  private focus: { x: number; z: number; radius: number } | null = null;

  constructor(url: string, private renderer: THREE.WebGPURenderer, private camera: THREE.PerspectiveCamera,
    private scene: THREE.Scene, private idPass = false) {
    this.ktx2 = new LoadingKtx().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
    this.current = this.create(url);
  }

  private configure(model: THREE.Object3D): number {
    const previous = this.configured.get(model);
    if (previous !== undefined) return previous;
    let tris = 0;
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true; mesh.receiveShadow = true;
      const geometry = mesh.geometry;
      tris += (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
    });
    if (!this.idPass) {
      const shadow = batchTileShadows(model);
      if (shadow) this.shadowBatches.set(model, shadow);
    }
    this.configured.set(model, tris);
    return tris;
  }

  /** The current tileset renderer. */
  get tiles(): TilesRenderer { return this.current.renderer; }

  private create(url: string, unchanged?: ReadonlySet<string>): Tileset {
    const renderer = new TilesRenderer(url);
    const pending = new Map<Tile, ReturnType<typeof loading.begin>>();
    renderer.addEventListener('tile-download-start', ({ tile, url }: { tile: Tile; url: string }) => { if (loading.running && !url.endsWith('.json')) pending.set(tile, loading.begin(`Visual tile ${url.split('/').slice(-2).join('/')}`)); });
    const set: Tileset = { renderer, l0Parents: new Map(), refinable: new Set(), loaded: 0 };
    if (unchanged) {
      set.reuse = new TileReuse(this.current.renderer, renderer, unchanged);
      renderer.registerPlugin(set.reuse);
    }
    if (this.focus) {
      set.region = new PlayerLoadRegion();
      set.region.radius = this.focus.radius;
      renderer.registerPlugin(set.region);
      set.region.move(this.focus.x, this.focus.z);
      set.region.retain(this.retainedStarts);
    }
    // The KTX2 loader (its transcoder workers) is shared by a swapped-in release: disposing the old tileset keeps it.
    renderer.registerPlugin(new GLTFExtensionsPlugin({ ktxLoader: this.ktx2, meshoptDecoder: MeshoptDecoder, autoDispose: false, plugins: [() => textureUploads(this.renderer).plugin()] }));
    // Keep the library's parse promise pending through final styling/compilation. REPLACE parents remain
    // visible, and original glTF resources stay owned by the loader even after material replacement.
    const parser = renderer as unknown as { parseTile(buffer: ArrayBuffer, tile: Tile, extension: string, url: string, signal: AbortSignal): Promise<void> };
    const parse = parser.parseTile.bind(renderer);
    parser.parseTile = async (buffer, tile, extension, url, signal) => {
      await parse(buffer, tile, extension, url, signal);
      const model = (tile as Tile & { engineData: { scene: THREE.Object3D | null } }).engineData.scene;
      if (!model || signal.aborted) return;
      model.parent = renderer.group; // include the tileset's Z-up -> engine-axis transform without drawing it
      model.updateWorldMatrix(true, true);
      this.configure(model);
      try { await this.prepareModel?.(model, tile, signal); }
      finally {
        if (signal.aborted) model.traverse(object => {
          const mesh = object as THREE.Mesh;
          if (!mesh.isMesh) return;
          mesh.geometry.dispose();
          for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
        });
      }
    };
    renderer.errorTarget = 8;
    // QA retains the nearest-L0 budget. In play the region plugin owns refinement and masks camera error.
    const viewer = renderer as unknown as { calculateTileViewError: ViewError };
    const viewError = viewer.calculateTileViewError.bind(renderer);
    viewer.calculateTileViewError = (tile, target) => {
      viewError(tile, target);
      if (set.region) { target.error = 0; return; } // the distance-region plugin owns play-mode refinement
      const id = tile.extras?.id as string | undefined;
      if (!id) return;
      if (!set.l0Parents.has(id)) {
        const children = (tile.children ?? []).map((c) => c.extras?.id as string | undefined)
          .filter((c): c is string => !!c && c.startsWith('t0_'));
        if (children.length) set.l0Parents.set(id, children);
      }
      if (set.l0Parents.has(id) && !set.refinable.has(id)) target.error = 0;
    };
    renderer.group.rotation.x = -Math.PI / 2;
    renderer.setCamera(this.camera);
    this.renderer.getDrawingBufferSize(this.resolution);
    renderer.setResolution(this.camera, this.resolution.x, this.resolution.y);
    renderer.addEventListener('load-model', (event: ModelEvent) => {
      pending.get(event.tile as Tile)?.(); pending.delete(event.tile as Tile);
      const model = event.scene;
      if (model.userData.releasePlaceholder) return;
      const tris = this.configure(model);
      this.triCount.set(model, tris);
      this.residentTris += tris; this.residentModels++; this.loadedModels++; set.loaded++;
      this.trackModel(model, String(event.tile.extras?.id ?? ''));
      this.events.dispatchEvent({ type: 'load-model', scene: model, tile: event.tile });
    });
    renderer.addEventListener('dispose-model', (event: ModelEvent) => {
      const model = event.scene;
      if (model.userData.releasePlaceholder) return;
      const shadow = this.shadowBatches.get(model);
      if (shadow) {
        disposeShadowBatch(shadow);
        this.shadowBatches.delete(model);
      }
      if (this.triCount.has(model)) {
        this.residentTris -= this.triCount.get(model)!; this.residentModels--; this.triCount.delete(model);
        this.untrackModel(model, String(event.tile.extras?.id ?? ''));
      }
      this.configured.delete(model);
      this.events.dispatchEvent({ type: 'dispose-model', scene: model, tile: event.tile });
    });
    renderer.addEventListener('load-error', ({ error, url: u, tile }: { error: Error; url: string | URL; tile: Tile }) => {
      pending.get(tile)?.(`FAILED: ${String(error)}`); pending.delete(tile);
      const msg = `tile load error ${String(u)}: ${error?.message ?? error}`;
      this.errors.push(msg); loading.note(msg); console.error(msg);
    });
    this.scene.add(renderer.group);
    return set;
  }

  /** Loads the release at `url` hidden beside the current tileset; resolves once it replaced it. */
  reload(url: string, unchanged?: ReadonlySet<string>): Promise<void> {
    if (this.next) this.drop(this.next);
    this.next = this.create(url, unchanged);
    this.next.renderer.group.visible = false;
    return new Promise(resolve => { this.swapped = resolve; });
  }

  private drop(set: Tileset) {
    set.renderer.dispose();
    set.renderer.group.removeFromParent();
  }

  private ready(set: Tileset) {
    if (set.region) return set.region.settled;
    return set.renderer.root !== null && (set.loaded + (set.reuse?.loaded ?? 0)) > 0 && set.renderer.loadProgress >= 1;
  }

  /** Play-mode distance ring. QA keeps its camera/SSE contract. Call with the player, not the orbit camera. */
  setFocus(x: number, z: number, radius: number) {
    this.focus = { x, z, radius };
    for (const set of this.next ? [this.current, this.next] : [this.current]) {
      if (!set.region) {
        set.region = new PlayerLoadRegion();
        set.renderer.registerPlugin(set.region);
      }
      set.region.radius = radius;
      set.region.move(x, z);
    }
  }

  /** Keep every selectable start resident until the player has entered the world. */
  retainStarts(points: readonly { x: number; z: number }[]) {
    this.retainedStarts = points;
    for (const set of this.next ? [this.current, this.next] : [this.current]) set.region?.retain(points);
  }

  get progress() { return this.current.region?.progress ?? { loaded: this.residentModels, total: this.residentModels }; }

  update(camera: THREE.PerspectiveCamera) {
    camera.updateMatrixWorld();
    // SSE is measured in actual output pixels on every backend, including HiDPI and capped render sizes.
    this.renderer.getDrawingBufferSize(this.resolution);
    for (const set of this.next ? [this.current, this.next] : [this.current]) {
      if (!set.region) set.refinable = refinableParents(set.l0Parents, camera.position.x, camera.position.z);
      if (this.focus) set.region?.move(this.focus.x, this.focus.z);
      set.renderer.setResolution(camera, this.resolution.x, this.resolution.y);
      set.renderer.update();
    }
    if (this.next && this.ready(this.next)) {
      const old = this.current;
      this.current = this.next;
      this.next = null;
      this.current.reuse?.transfer();
      this.current.renderer.group.visible = true;
      this.drop(old);
      this.swaps++;
      this.swapped?.();
      this.swapped = null;
    }
  }

  /** Required ring models are resident in play; QA keeps its current-view settled contract. */
  get settled(): boolean {
    if (this.current.region) return this.current.region.settled;
    return this.tiles.root !== null && this.loadedModels > 0 && this.tiles.loadProgress >= 1;
  }
}
