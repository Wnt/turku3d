import * as THREE from 'three/webgpu';

/** Static large surfaces only; actor, vegetation, pole and small-prop work stays out of the river pass. */
export const REFLECTION_LAYER = 2;
const SURFACES = new Set(['ground', 'buildings', 'structures', 'massing', 'roads', 'boats', 'bridges']);
export function reflectionSurface(mesh: THREE.Mesh): boolean {
  if (mesh.userData.reflection_surface === false) return false;
  if (mesh.userData.shadowBatch || (mesh as THREE.InstancedMesh).isInstancedMesh) return false;
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  if (materials.some(material => material.transparent || (material as THREE.MeshPhysicalMaterial).transmission > 0)) return false;
  if (mesh.userData.prop_type || mesh.userData.objects?.some((row: Record<string, unknown>) => row.prop_type)) return false;
  // Thin opaque quay LED bars are intentional reflected light sources, whatever their bounds.
  if (mesh.userData.material_key === 'night_quay_bar'
      || materials.some(material => material.userData.material_key === 'night_quay_bar')) return true;
  if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
  // A four-metre diameter keeps facades, banks and road surfaces, not individual trim/rail pieces.
  if ((mesh.geometry.boundingSphere?.radius ?? 0) * mesh.matrixWorld.getMaxScaleOnAxis() < 2) return false;
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    if (node.userData.material_key === 'water' || node.userData.material_key === 'river') return false;
    if (node.userData.layer) return SURFACES.has(node.userData.layer);
  }
  return false;
}

/** CSM is fitted to the primary camera. Reflections sample those same maps instead of drawing them again. */
export class ReflectionShadows {
  private automatic: boolean[] = [];
  private dirty: boolean[] = [];
  private paused: (THREE.LightShadow | undefined)[] = [];
  pause(lights: readonly { shadow?: THREE.LightShadow }[]) {
    this.paused.length = lights.length;
    for (let i = 0; i < lights.length; i++) {
      const shadow = this.paused[i] = lights[i].shadow;
      if (!shadow) continue;
      this.automatic[i] = shadow.autoUpdate; this.dirty[i] = shadow.needsUpdate;
      shadow.autoUpdate = false; shadow.needsUpdate = false;
    }
  }
  restore(lights: readonly { shadow?: THREE.LightShadow }[]) {
    for (let i = 0; i < this.paused.length; i++) {
      const shadow = this.paused[i];
      if (!shadow || lights[i]?.shadow !== shadow) continue;
      shadow.autoUpdate = this.automatic[i]; shadow.needsUpdate = this.dirty[i];
    }
  }
}

/** A moving camera re-renders the reflection every frame: an image from an older pose visibly trails
 * the view. Only a still camera reuses it, refreshing at `slowFps` for moving water and actors. */
export class ReflectionCadence {
  private last = -Infinity;
  private pose = new THREE.Matrix4();
  constructor(private slowFps: number) {}

  shouldUpdate(camera: THREE.Camera, now: number, hasOutput: boolean, force = false): boolean {
    const moved = !camera.matrixWorld.equals(this.pose);
    if (hasOutput && !force && !moved && now - this.last < 1000 / this.slowFps) return false;
    this.last = now;
    this.pose.copy(camera.matrixWorld);
    return true;
  }
}

/** Bench lab `reflect=`: objects farther than this (m, horizontal, to their bounds) from the eye stay out of the river
 *  reflection. Infinity keeps the coverage test alone. */
export const REFLECTION_RANGE = { maxDistance: Infinity };
const FINE = 8, WAVE_MARGIN = 4;
// Non-negative packed cell index for |x|, |z| < 32768 cells (262 km at 8 m).
const cellKey = (x: number, z: number) => (x + 32768) * 65536 + (z + 32768);
const triangle = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
const center = new THREE.Vector3(), eyePosition = new THREE.Vector3();

/** Where a reach's mirror plane is real water, on an 8 m world XZ grid with a summed-area table for O(1) box queries,
 * so the reflection pass draws only what the water can show (census at spawn: the reflection drew 167 of 720 draws). */
export class WaterCoverage {
  private fine = new Map<number, number>();
  private cells = new Map<THREE.Mesh, number[]>();
  private table: Int32Array | null = null;
  private x0 = 0; private z0 = 0; private width = 0; private height = 0;

  add(mesh: THREE.Mesh) {
    if (this.cells.has(mesh)) return;
    mesh.updateWorldMatrix(true, false);
    const position = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
    const marked = new Set<number>();
    const count = index ? index.count : position.count;
    for (let i = 0; i + 2 < count; i += 3) {
      for (let j = 0; j < 3; j++) triangle[j].fromBufferAttribute(position, index ? index.getX(i + j) : i + j).applyMatrix4(mesh.matrixWorld);
      this.rasterize(marked);
    }
    const keys = [...marked];
    for (const key of keys) this.fine.set(key, (this.fine.get(key) ?? 0) + 1);
    this.cells.set(mesh, keys); this.table = null;
  }

  remove(mesh: THREE.Mesh) {
    const keys = this.cells.get(mesh);
    if (!keys) return;
    for (const key of keys) { const n = this.fine.get(key)! - 1; if (n > 0) this.fine.set(key, n); else this.fine.delete(key); }
    this.cells.delete(mesh); this.table = null;
  }

  get empty() { return this.fine.size === 0; }

  /** Whether a mirror image of the world sphere can land on water as seen from `eye` (plane height `level`).
   * A point at height h above the plane is seen in it where the eye-to-mirror-point line crosses the plane:
   * eye.xz + (p.xz - eye.xz) * hEye / (hEye + h). Over the sphere's bounding box the crossing points span the box
   * from its corners at the smallest and largest heights; it is widened for wave distortion of the reflection UVs. */
  reflects(sphereCenter: THREE.Vector3, radius: number, eye: THREE.Vector3, level: number) {
    if (this.empty) return true;
    const hEye = eye.y - level;
    if (hEye <= 0.05) return true;
    const top = sphereCenter.y + radius - level;
    if (top <= 0) return false;
    const bottom = Math.max(sphereCenter.y - radius - level, 0);
    const near = hEye / (hEye + top), far = hEye / (hEye + bottom);
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const k of [near, far]) for (const sx of [-radius, radius]) for (const sz of [-radius, radius]) {
      const x = eye.x + (sphereCenter.x + sx - eye.x) * k, z = eye.z + (sphereCenter.z + sz - eye.z) * k;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (z < z0) z0 = z; if (z > z1) z1 = z;
    }
    return this.any(x0 - WAVE_MARGIN, x1 + WAVE_MARGIN, z0 - WAVE_MARGIN, z1 + WAVE_MARGIN);
  }

  /** The reflection draw test for one object; objects without bounds (sky) always draw. */
  reflectsObject(object: THREE.Object3D, eye: THREE.Object3D, level: number) {
    const geometry = (object as THREE.Mesh).geometry;
    if (!geometry || object.frustumCulled === false) return true;
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    const sphere = geometry.boundingSphere;
    if (!sphere || !Number.isFinite(sphere.radius)) return true;
    center.copy(sphere.center).applyMatrix4(object.matrixWorld);
    const radius = sphere.radius * object.matrixWorld.getMaxScaleOnAxis();
    eyePosition.setFromMatrixPosition(eye.matrixWorld);
    if (REFLECTION_RANGE.maxDistance < Infinity
      && Math.hypot(center.x - eyePosition.x, center.z - eyePosition.z) - radius > REFLECTION_RANGE.maxDistance) return false;
    return this.reflects(center, radius, eyePosition, level);
  }

  private any(x0: number, x1: number, z0: number, z1: number) {
    const table = this.table ?? this.build();
    const ax = Math.max(Math.floor(x0 / FINE) - this.x0, 0), bx = Math.min(Math.floor(x1 / FINE) - this.x0, this.width - 1);
    const az = Math.max(Math.floor(z0 / FINE) - this.z0, 0), bz = Math.min(Math.floor(z1 / FINE) - this.z0, this.height - 1);
    if (ax > bx || az > bz) return false;
    const row = this.width + 1, at = (x: number, z: number) => table[z * row + x];
    return at(bx + 1, bz + 1) - at(ax, bz + 1) - at(bx + 1, az) + at(ax, az) > 0;
  }

  /** Summed-area table over the cells' bounding grid, rebuilt only after water was added or removed. */
  private build() {
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const key of this.fine.keys()) {
      const x = Math.floor(key / 65536) - 32768, z = key % 65536 - 32768;
      if (x < minX) minX = x; if (x > maxX) maxX = x; if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    this.x0 = minX; this.z0 = minZ; this.width = maxX - minX + 1; this.height = maxZ - minZ + 1;
    const row = this.width + 1, table = new Int32Array(row * (this.height + 1));
    for (const key of this.fine.keys()) {
      const x = Math.floor(key / 65536) - 32768 - minX, z = key % 65536 - 32768 - minZ;
      table[(z + 1) * row + x + 1] = 1;
    }
    for (let z = 1; z <= this.height; z++) for (let x = 1; x <= this.width; x++)
      table[z * row + x] += table[(z - 1) * row + x] + table[z * row + x - 1] - table[(z - 1) * row + x - 1];
    return this.table = table;
  }

  /** Marks every 8 m cell a triangle touches: cells whose centre lies within the triangle grown by a cell diagonal. */
  private rasterize(marked: Set<number>) {
    const [a, b, c] = triangle;
    const minX = Math.min(a.x, b.x, c.x), maxX = Math.max(a.x, b.x, c.x), minZ = Math.min(a.z, b.z, c.z), maxZ = Math.max(a.z, b.z, c.z);
    const grow = FINE * Math.SQRT1_2;
    const edges = [[a, b], [b, c], [c, a]] as const;
    const area = (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
    for (let x = Math.floor((minX - grow) / FINE); x <= Math.floor((maxX + grow) / FINE); x++) {
      for (let z = Math.floor((minZ - grow) / FINE); z <= Math.floor((maxZ + grow) / FINE); z++) {
        const px = (x + 0.5) * FINE, pz = (z + 0.5) * FINE;
        let inside = true;
        for (const [p, q] of edges) {
          const ex = q.x - p.x, ez = q.z - p.z, length = Math.hypot(ex, ez) || 1;
          const side = (ex * (pz - p.z) - ez * (px - p.x)) * Math.sign(area || 1) / length; // signed distance, + inside
          if (side < -grow) { inside = false; break; }
        }
        if (inside) marked.add(cellKey(x, z));
      }
    }
  }
}

/** Carried by a river reflector's virtual camera (water.ts) for the reflection draw test in GameRenderer. */
export interface WaterReflection { coverage: WaterCoverage; eye: THREE.Object3D; plane: THREE.Object3D }
