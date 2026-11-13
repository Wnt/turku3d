// Instance rows drawn as instances: one InstancedMesh per library mesh for every entity of a location that is drawn
// from its rows right now, so a library model is uploaded once and costs one draw per pass however many entities
// place it. Entities enter and leave (a tree cell came near, or went far and draws its far mesh instead) and the
// pool writes its instance buffers again: a few thousand matrices.
//
// Why not one InstancedMesh per entity and library mesh: a draw costs the renderer's CPU about as much whatever it
// draws, and the 154 tree cells of one location were 4,000 such meshes (measured 2026-11-13: slower than 26 million
// merged triangles).
import * as THREE from 'three/webgpu';
import { shareInstanceProgram } from '../../render/instancePrograms';
import { disposeDraw } from '../../render/sharedMaterials';

/** The instances of one library mesh in one entity, as world matrices (16 floats each). */
export interface Rows { key: string; geometry: THREE.BufferGeometry; material: THREE.Material; matrices: Float32Array }
/** One library mesh and the most instances the location can draw of it. */
export interface Part { key: string; geometry: THREE.BufferGeometry; material: THREE.Material; count: number }

export class InstancePool {
  /** Stays at the origin of the world: the rows are world matrices. */
  readonly group = new THREE.Group();
  private meshes = new Map<string, THREE.InstancedMesh>();
  private twins = new Map<string, THREE.InstancedMesh>();
  private members = new Map<string, readonly Rows[]>();
  private dirty = false;

  /** `shadowLayer`: the members also cast shadows, in the cameras that see this layer and in no other. */
  constructor(name: string, private layer: string, private shadowLayer: number | null) {
    this.group.name = name; this.group.userData = { layer };
  }

  /** Empty meshes for `parts`; the members are kept. Style and prepare `group`, then call `finish`. */
  build(parts: Iterable<Part>) {
    this.release();
    for (const part of parts) {
      const mesh = new THREE.InstancedMesh(part.geometry, part.material, Math.max(part.count, 1));
      mesh.name = `pool:${part.key}`; mesh.userData = { layer: this.layer };
      mesh.count = 0; mesh.frustumCulled = false;
      // Every pool mesh draws with the programs of its material and vertex layout, not with ones of its own.
      shareInstanceProgram(mesh, true);
      this.meshes.set(part.key, mesh);
      this.group.add(mesh);
    }
    this.dirty = true;
  }

  /** After the group is styled and prepared: who casts shadows. The twins share geometry, material and matrices. */
  finish() {
    for (const [key, mesh] of this.meshes) {
      mesh.castShadow = false; mesh.receiveShadow = true;
      if (this.shadowLayer === null) continue;
      const twin = new THREE.InstancedMesh(mesh.geometry, mesh.material, 1);
      twin.instanceMatrix = mesh.instanceMatrix;
      twin.name = `${mesh.name}-shadow`; twin.userData = { layer: this.layer, shadowBatch: true };
      shareInstanceProgram(twin, true);
      twin.layers.set(this.shadowLayer); twin.castShadow = true; twin.receiveShadow = false; twin.frustumCulled = false;
      twin.count = mesh.count; twin.visible = mesh.visible;
      this.twins.set(key, twin);
      this.group.add(twin);
    }
  }

  /** The entity draws from these rows, or (null) no longer does. Takes effect at the next `update`. */
  set(id: string, rows: readonly Rows[] | null) {
    if (rows) { if (this.members.get(id) === rows) return; this.members.set(id, rows); }
    else if (!this.members.delete(id)) return;
    this.dirty = true;
  }

  /** Writes the instance buffers when the members changed. */
  update() {
    if (!this.dirty) return;
    this.dirty = false;
    for (const mesh of this.meshes.values()) mesh.count = 0;
    for (const rows of this.members.values()) for (const row of rows) {
      const mesh = this.meshes.get(row.key), count = row.matrices.length / 16;
      // A row the pool was not built for (the location changed under it) waits for the next build.
      if (!mesh || mesh.count + count > mesh.instanceMatrix.count) continue;
      (mesh.instanceMatrix.array as Float32Array).set(row.matrices, mesh.count * 16);
      mesh.count += count;
    }
    for (const [key, mesh] of this.meshes) {
      mesh.instanceMatrix.needsUpdate = true; mesh.visible = mesh.count > 0;
      const twin = this.twins.get(key);
      if (twin) { twin.count = mesh.count; twin.visible = mesh.visible; }
    }
  }

  /** Drops the draws; the geometry and materials belong to the library. */
  release() {
    for (const mesh of [...this.meshes.values(), ...this.twins.values()]) { mesh.removeFromParent(); disposeDraw(mesh); }
    this.meshes.clear(); this.twins.clear();
  }
}
