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

/** Props (breakable poles, signs, bus stops: world/poles.ts moves their nodes when they break) keep updating. */
const movable = (object: THREE.Object3D) => !!object.userData.prop_type
  || (object.userData.objects as Record<string, unknown>[] | undefined)?.some(row => row.prop_type) === true;

/** A published tile or scenery model's local transforms never change: compose each once and stop recomposing them
 *  every frame (round 9 CPU trace: updateMatrixWorld 7.3 % of main-thread time). With the scene root not forcing
 *  (`scene.matrixAutoUpdate = false`), the per-frame world update then visits frozen nodes without any matrix work,
 *  while every automatic node (added later: pole pivots, attached placements) still composes and updates its own
 *  subtree. Kept automatic: prop subtrees, non-mesh leaves (the river's reflection target, which moves between
 *  reaches) and StaticGroups (their own freeze). */
export function freezeModel(model: THREE.Object3D) {
  model.updateMatrixWorld(true);
  const visit = (object: THREE.Object3D) => {
    if (movable(object) || object instanceof StaticGroup) return;
    if (object !== model && !(object as THREE.Mesh).isMesh && !object.children.length) return;
    if (object.matrixAutoUpdate) { object.updateMatrix(); object.matrixAutoUpdate = false; }
    for (const child of object.children) visit(child);
  };
  visit(model);
}
