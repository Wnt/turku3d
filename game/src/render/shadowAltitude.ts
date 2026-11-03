// GTA V's altitude rule for sun shadows (loops/techstudy/REPORT.md §7 L3): above about 100 m camera height its shadow
// draws fall from 665 to 238, because small casters leave the shadow passes. Their shadows are a few pixels wide
// from up there, while every one of them still costs a draw in each cascade.
// Above OFF_M over the ground (back below ON_M), these casters stop casting: vegetation, facade relief modules, props,
// and any caster whose single instance is under SMALL_M in bounding radius (cars, walkers, poles, signs, small
// shadow chunks). Buildings, structures, terrain and tall masts keep casting.
import type * as THREE from 'three/webgpu';

/** Tile vegetation (world/vegetationLod.ts vegetationOwner, inlined: no world import from the renderer). */
function vegetation(object: THREE.Object3D) {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) if (node.userData.layer === 'vegetation') return true;
  return false;
}

export const ALTITUDE_SHADOWS = { offM: 100, onM: 90, smallM: 6 } as const;

/** Whether `object` is a small caster that may leave the shadow passes at altitude. */
export function smallCaster(object: THREE.Object3D): boolean {
  const data = object.userData;
  if (data.nature || data.facadeModules || data.prop_type || vegetation(object)
    || (data.objects as Record<string, unknown>[] | undefined)?.some(row => row.prop_type)) return true;
  const geometry = (object as THREE.Mesh).geometry;
  if (!geometry) return false;
  // An InstancedMesh's geometry is one instance; its own bounds would span the whole batch.
  if (!geometry.boundingSphere) geometry.computeBoundingSphere();
  const radius = geometry.boundingSphere?.radius;
  return radius !== undefined && Number.isFinite(radius) && radius * object.matrixWorld.getMaxScaleOnAxis() < ALTITUDE_SHADOWS.smallM;
}

export class AltitudeShadows {
  /** Whether the camera is high enough that small casters are skipped. */
  high = false;
  enabled = true;
  private small = new WeakMap<THREE.Object3D, boolean>();

  /** Once per frame: the camera's height above the ground under it (m). */
  update(height: number) {
    this.high = this.enabled && Number.isFinite(height)
      && (this.high ? height > ALTITUDE_SHADOWS.onM : height > ALTITUDE_SHADOWS.offM);
  }

  /** Whether a sun-shadow draw of `object` is skipped this frame. */
  skips(object: THREE.Object3D) {
    if (!this.high) return false;
    let small = this.small.get(object);
    if (small === undefined) this.small.set(object, small = smallCaster(object));
    return small;
  }
}

export const altitudeShadows = new AltitudeShadows();
