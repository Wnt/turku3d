// Static render bundles: a resident tile's unchanging draws are recorded once per camera and pass (r186 BundleGroup,
// GPURenderBundle) and replayed, instead of being projected, sorted and encoded draw by draw every frame.
//
// A BundleGroup's children are projected only when it records, so the group does its own culling: before every pass
// (scene.onBeforeRender) each group's world bounds are tested against that pass's camera frustum and, for river
// reflections, against the water it can land on. Recording bakes everything a draw decides, so the children are
// checked once per frame and any change (visibility, material, geometry, shadow flags, layers, removal) records again.
// Draws that change by themselves stay out: placements and block shells (occlusion, band swaps), props, instancing,
// vegetation, transparent or cutout materials and anything with its own render callbacks.
import * as THREE from 'three/webgpu';
import { lodFades } from './lodFade';
import type { WaterReflection } from './reflections';

/** Layers of tile content that never change after its install. */
const STATIC_LAYERS = new Set(['ground', 'markings', 'structures', 'buildings']);
const beforeRender = THREE.Object3D.prototype.onBeforeRender, afterRender = THREE.Object3D.prototype.onAfterRender;
const updateMatrixWorld = THREE.Object3D.prototype.updateMatrixWorld;
/** Tiles bundled per frame: each records its bundles on its first frame in every pass. */
const ADOPT_PER_FRAME = 4;

function plainDraw(object: THREE.Object3D): object is THREE.Mesh {
  const mesh = object as THREE.Mesh, material = mesh.material as THREE.Material & { transmission?: number };
  if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || (mesh as THREE.SkinnedMesh).isSkinnedMesh
    || (mesh as unknown as { isBatchedMesh?: boolean }).isBatchedMesh || !mesh.visible || Array.isArray(material) || !material
    || material.transparent || material.alphaTest > 0 || (material.transmission ?? 0) > 0
    || Object.keys(mesh.geometry.morphAttributes).length || mesh.onBeforeRender !== beforeRender || mesh.onAfterRender !== afterRender
    // Placement batches follow placement visibility and worker occlusion every frame (world/staticBatch.ts).
    || mesh.userData.pooledVegetation || mesh.userData.placementIds || mesh.userData.prop_type
    || (mesh.userData.objects as Record<string, unknown>[] | undefined)?.some(row => row.prop_type)) return false;
  return true;
}

/** A direct child of a tile root that may be recorded: one static surface, or a shadow batch with its chunks. */
export function bundleable(object: THREE.Object3D): boolean {
  if (!plainDraw(object)) return false;
  if (object.userData.shadowBatch) return object.children.every(chunk => chunk.userData.shadowBatch && plainDraw(chunk) && !chunk.children.length);
  return !object.children.length && STATIC_LAYERS.has(object.userData.layer as string);
}

interface Member {
  object: THREE.Mesh; parent: THREE.Object3D;
  /** The member and its chunks as they were before bundling: culling and transform updates are restored on release. */
  nodes: { node: THREE.Object3D; frustumCulled: boolean; matrixAutoUpdate: boolean }[];
  visible: boolean; material: THREE.Material; version: number; geometry: THREE.BufferGeometry;
  castShadow: boolean; receiveShadow: boolean; layers: number; renderOrder: number;
}

/** A tile's bundled draws. Its transforms are frozen like StaticGroup's (world/frozenTransforms.ts): the members are
 *  composed once and multiplied again only when the tile root's world matrix really changes. */
export class TileBundle extends THREE.BundleGroup {
  readonly members: Member[] = [];
  /** World-space bounds of every member, refreshed when the tile moves. */
  readonly bounds = new THREE.Box3();
  readonly sphere = new THREE.Sphere();
  private readonly parentWorld = new THREE.Matrix4().set(NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN);
  private boundsWorld = new THREE.Matrix4().set(NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN);
  constructor() { super(); this.matrixAutoUpdate = false; this.userData.tileBundle = true; }

  override updateMatrixWorld(force?: boolean) {
    const parent = this.parent;
    if (!this.matrixWorldNeedsUpdate && parent && this.parentWorld.equals(parent.matrixWorld)) return;
    if (parent) this.parentWorld.copy(parent.matrixWorld);
    updateMatrixWorld.call(this, true);
    void force;
  }

  /** Bounds follow the tile root (a floating origin or a moved tile). */
  refreshBounds() {
    if (this.boundsWorld.equals(this.matrixWorld)) return;
    this.boundsWorld.copy(this.matrixWorld);
    this.bounds.makeEmpty();
    for (const { object } of this.members) this.bounds.expandByObject(object);
    this.bounds.getBoundingSphere(this.sphere);
  }

  /** Whether a member changed since it was recorded: then every bundle of the tile records again. Members something
   *  else removed (a released batch) leave the bundle. */
  changed() {
    let changed = false;
    for (let i = this.members.length - 1; i >= 0; i--) {
      const m = this.members[i], o = m.object;
      if (o.parent !== this) { this.members.splice(i, 1); changed = true; continue; }
      const material = o.material as THREE.Material;
      if (o.visible !== m.visible || material !== m.material || material.version !== m.version || o.geometry !== m.geometry
        || o.castShadow !== m.castShadow || o.receiveShadow !== m.receiveShadow || o.layers.mask !== m.layers
        || o.renderOrder !== m.renderOrder) {
        Object.assign(m, snapshot(o)); changed = true;
      }
    }
    if (changed) { this.boundsWorld.elements[0] = NaN; this.needsUpdate = true; }
    return changed;
  }
}

function snapshot(o: THREE.Mesh) {
  const material = o.material as THREE.Material;
  return { visible: o.visible, material, version: material.version, geometry: o.geometry, castShadow: o.castShadow,
    receiveShadow: o.receiveShadow, layers: o.layers.mask, renderOrder: o.renderOrder };
}

const frustum = new THREE.Frustum(), projection = new THREE.Matrix4(), eye = new THREE.Vector3();

/** Static render bundles of resident tiles (one TileBundle per tile root), culled per pass. */
export class StaticBundles {
  private pending = new Set<THREE.Object3D>();
  readonly bundles = new Map<THREE.Object3D, TileBundle>();
  readonly stats = { tiles: 0, members: 0, records: 0, culled: 0 };
  private previous: THREE.Scene['onBeforeRender'];

  constructor(private scene: THREE.Scene) {
    this.previous = scene.onBeforeRender;
    // r186 calls a scene's onBeforeRender in every render of it, after its matrices and camera are updated and before
    // projection: (renderer, scene, camera, renderTarget).
    const cull = this.cull.bind(this), previous = this.previous as unknown as (...args: unknown[]) => void;
    scene.onBeforeRender = function (this: THREE.Scene, ...args: unknown[]) {
      previous.apply(this, args);
      cull(args[2] as THREE.Camera);
    } as THREE.Scene['onBeforeRender'];
  }

  /** A tile model was published; it is bundled once it is in the scene and its install dissolve has landed. */
  add(model: THREE.Object3D) {
    if (model.userData.scenery || this.bundles.has(model)) return;
    this.pending.add(model);
  }

  /** Restore a tile's members to its root (before its batches and surfaces are released). */
  remove(model: THREE.Object3D) {
    this.pending.delete(model);
    const bundle = this.bundles.get(model);
    if (!bundle) return;
    this.bundles.delete(model);
    for (const m of bundle.members) {
      if (m.object.parent !== bundle) continue;
      m.parent.add(m.object);
      for (const { node, frustumCulled, matrixAutoUpdate } of m.nodes) { node.frustumCulled = frustumCulled; node.matrixAutoUpdate = matrixAutoUpdate; }
    }
    bundle.removeFromParent();
    bundle.clear();
    this.stats.tiles = this.bundles.size;
  }

  /** Once per frame before rendering: adopt landed tiles and record again whatever changed. */
  update() {
    let adopted = 0;
    for (const model of this.pending) {
      if (adopted >= ADOPT_PER_FRAME) break;
      if (!model.parent || lodFades.has(model)) continue;
      this.pending.delete(model);
      this.bundle(model);
      adopted++;
    }
    let members = 0;
    for (const bundle of this.bundles.values()) {
      if (bundle.changed()) this.stats.records++;
      bundle.refreshBounds();
      members += bundle.members.length;
    }
    this.stats.members = members;
  }

  private bundle(model: THREE.Object3D) {
    const bundle = new TileBundle();
    const members = model.children.filter(bundleable) as THREE.Mesh[];
    if (!members.length) return;
    model.updateWorldMatrix(true, true);
    for (const object of members) {
      const nodes: Member['nodes'] = [];
      object.traverse(node => {
        nodes.push({ node, frustumCulled: node.frustumCulled, matrixAutoUpdate: node.matrixAutoUpdate });
        // Recording projects every member whatever the camera: the group culls as a whole.
        node.updateMatrix(); node.matrixAutoUpdate = false; node.frustumCulled = false;
      });
      bundle.members.push({ object, parent: object.parent!, nodes, ...snapshot(object) });
      bundle.add(object);
    }
    model.add(bundle);
    bundle.updateMatrixWorld(true);
    bundle.refreshBounds();
    this.bundles.set(model, bundle);
    this.stats.tiles = this.bundles.size;
  }

  /** Per pass: a group draws only if its bounds meet this pass's view (and, in a river reflection, its water). */
  private cull(camera: THREE.Camera) {
    if (!this.bundles.size) return;
    projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    frustum.setFromProjectionMatrix(projection, camera.coordinateSystem, (camera as { reversedDepth?: boolean }).reversedDepth);
    const water = (camera.userData as { waterReflection?: WaterReflection }).waterReflection;
    const level = water?.plane.matrixWorld.elements[13] ?? 0;
    if (water) eye.setFromMatrixPosition(water.eye.matrixWorld);
    let culled = 0;
    for (const bundle of this.bundles.values()) {
      const visible = !bundle.bounds.isEmpty() && frustum.intersectsBox(bundle.bounds)
        && (!water || water.coverage.reflects(bundle.sphere.center, bundle.sphere.radius, eye, level));
      bundle.visible = visible;
      if (!visible) culled++;
    }
    this.stats.culled = culled;
  }

  dispose() {
    for (const model of [...this.bundles.keys()]) this.remove(model);
    this.pending.clear();
    this.scene.onBeforeRender = this.previous;
  }
}
