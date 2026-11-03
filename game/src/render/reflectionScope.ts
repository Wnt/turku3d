// What a river reflection render may skip without changing a pixel of it. r186 walks the whole scene graph for every
// render: the reflection camera sees only REFLECTION_LAYER, yet its projection visited every tile, placement,
// vegetation part and scenery cell (round 9 CPU trace: _projectObject 2.4 s of self time inside the reflection).
// Around each reflection render, these are hidden and then restored, all decided per subtree rather than per object:
// - subtrees with no object on the reflection layer, which r186 would walk but never draw (layers gate pushing
//   only), cached per published model at install;
// - published models and placement-sized parts whose bounds fail the same water test as the per-draw cull
//   (WaterCoverage.reflects is monotone in the sphere: a containing sphere that cannot land on water means no member
//   can);
// - models outside the reflection camera's frustum, unless they hold unculled reflective draws.
import * as THREE from 'three/webgpu';
import { REFLECTION_LAYER, type WaterReflection } from './reflections';

const layer = new THREE.Layers(); layer.set(REFLECTION_LAYER);
const member = new THREE.Sphere();

/** What a reflection render could draw from `root`: whether anything is on the reflection layer, whether that
 *  includes a light (it lights everything else) or a draw three never culls, and the union of the reflective
 *  draws' own bounding spheres, exactly as the per-draw tests see them (frustum: Mesh/InstancedMesh spheres; water:
 *  WaterCoverage.reflectsObject's geometry sphere). */
function survey(root: THREE.Object3D) {
  let reflective = false, lit = false, unculled = false, meshes = 0;
  const sphere = new THREE.Sphere(new THREE.Vector3(), -1);
  root.traverse(o => {
    if (!o.layers.test(layer)) return;
    reflective = true;
    if ((o as THREE.Light).isLight) { lit = true; return; }
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    meshes++;
    if (!mesh.frustumCulled) { unculled = true; return; }
    const instanced = mesh as unknown as THREE.InstancedMesh;
    if (instanced.isInstancedMesh) {
      if (!instanced.boundingSphere) instanced.computeBoundingSphere();
      member.copy(instanced.boundingSphere!).applyMatrix4(mesh.matrixWorld);
      if (sphere.isEmpty()) sphere.copy(member); else sphere.union(member);
    }
    if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
    const own = mesh.geometry.boundingSphere;
    if (!own || !Number.isFinite(own.radius)) { unculled = true; return; }
    member.center.copy(own.center).applyMatrix4(mesh.matrixWorld);
    member.radius = own.radius * mesh.matrixWorld.getMaxScaleOnAxis();
    if (sphere.isEmpty()) sphere.copy(member); else sphere.union(member);
  });
  return { reflective, lit, unculled, meshes, sphere };
}

interface Part { object: THREE.Object3D; sphere: THREE.Sphere }
interface Model { children: number; frame: number; reflective: boolean; fixed: boolean; sphere: THREE.Sphere; dark: THREE.Object3D[]; parts: Part[] }
/** Models are measured again after this many reflection renders: a facade refresh may change a placement in place. */
const REMEASURE_FRAMES = 300;
/** A reflective child with at least this many meshes gets its own water test (placements, block shells). */
const PART_MESHES = 4;
const eye = new THREE.Vector3(), frustum = new THREE.Frustum(), projection = new THREE.Matrix4();

export class ReflectionScope {
  private models = new Map<THREE.Object3D, Model | null>();
  /** Scene children that are not published models (population, pools, sprites): reflective or not, by child count. */
  private others = new WeakMap<THREE.Object3D, { children: number; reflective: boolean; frame: number }>();
  private hidden: THREE.Object3D[] = [];
  private frame = 0;
  readonly stats = { hidden: 0, models: 0 };

  /** A published tile or scenery model (VisualTiles load-model); measured on its first reflection render. */
  add(model: THREE.Object3D) { if (!this.models.has(model)) this.models.set(model, null); }
  remove(model: THREE.Object3D) { this.models.delete(model); }

  private measure(model: THREE.Object3D): Model {
    model.updateWorldMatrix(true, true);
    const dark: THREE.Object3D[] = [], parts: Part[] = [];
    for (const child of model.children) {
      const part = survey(child);
      if (!part.reflective) { dark.push(child); continue; }
      if (part.meshes >= PART_MESHES && !part.lit && !part.unculled && !part.sphere.isEmpty()) parts.push({ object: child, sphere: part.sphere });
    }
    const whole = survey(model);
    // A model with a light or an unculled reflective draw never leaves as a whole (its dark children still do).
    return { children: model.children.length, frame: this.frame, reflective: whole.reflective, fixed: whole.lit || whole.unculled || whole.sphere.isEmpty(),
      sphere: whole.sphere, dark, parts };
  }

  private hide(object: THREE.Object3D) {
    if (!object.visible) return;
    object.visible = false;
    this.hidden.push(object);
  }

  /** Before a reflection render of `scene` with `camera`: hide what cannot appear in it. */
  begin(scene: THREE.Object3D, camera: THREE.Camera, water: WaterReflection) {
    this.frame++;
    const level = water.plane.matrixWorld.elements[13];
    eye.setFromMatrixPosition(water.eye.matrixWorld);
    projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    frustum.setFromProjectionMatrix(projection, camera.coordinateSystem, (camera as { reversedDepth?: boolean }).reversedDepth);
    const reflects = (s: THREE.Sphere) => water.coverage.reflects(s.center, s.radius, eye, level);
    for (const child of scene.children) {
      if (!child.visible) continue;
      if (this.models.has(child)) {
        let model = this.models.get(child)!;
        // Children added since (placements attached later) are measured again; tiles never move.
        if (!model || model.children !== child.children.length || this.frame - model.frame > REMEASURE_FRAMES)
          this.models.set(child, model = this.measure(child));
        if (!model.reflective || (!model.fixed && (!reflects(model.sphere) || !frustum.intersectsSphere(model.sphere)))) { this.hide(child); continue; }
        for (const dark of model.dark) this.hide(dark);
        for (const part of model.parts) if (!reflects(part.sphere)) this.hide(part.object);
        continue;
      }
      // Other scene children: their reflective flag is checked again when their child count changes, or every 2 s.
      let other = this.others.get(child);
      if (!other || other.children !== child.children.length || this.frame - other.frame > 120) {
        this.others.set(child, other = { children: child.children.length, reflective: survey(child).reflective, frame: this.frame });
      }
      if (!other.reflective) this.hide(child);
    }
    this.stats.hidden = this.hidden.length; this.stats.models = this.models.size;
  }

  /** After the reflection render: everything hidden is shown again. */
  end() {
    for (const object of this.hidden) object.visible = true;
    this.hidden.length = 0;
  }
}
