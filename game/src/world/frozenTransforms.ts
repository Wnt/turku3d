import * as THREE from 'three/webgpu';

const updateMatrixWorld = THREE.Object3D.prototype.updateMatrixWorld;

/** The wrapper of a placed subtree whose local transforms never change after placement (a building clone).
 * Ancestors auto-update, so r186's per-frame scene.updateMatrixWorld() forces every object below to compose and
 * multiply its matrices again each frame, visible or not. After `freeze()` the subtree is composed once and then
 * recomputed only when the parent's world matrix really changed or `matrixWorldNeedsUpdate` is set.
 * updateWorldMatrix() is unchanged. A prototype method (not a per-object closure) keeps the call sites monomorphic:
 * spawn-sized scene of 120 tiles x 35 placements, 2.1-3.7 -> 1.4-1.7 ms per frame (work/bench/matrix-world2.mjs). */
export class StaticGroup extends THREE.Group {
  private readonly parentWorld = new THREE.Matrix4().set(NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN);

  /** Compose every local matrix of the subtree now; later edits must set `matrixWorldNeedsUpdate` on this group. */
  freeze() {
    this.traverse(object => { object.updateMatrix(); object.matrixAutoUpdate = false; });
    return this;
  }

  override updateMatrixWorld(force?: boolean) {
    if (this.matrixAutoUpdate) { updateMatrixWorld.call(this, force); return; } // not frozen yet
    const parent = this.parent;
    if (!this.matrixWorldNeedsUpdate && parent && this.parentWorld.equals(parent.matrixWorld)) return;
    if (parent) this.parentWorld.copy(parent.matrixWorld);
    updateMatrixWorld.call(this, true);
  }
}
