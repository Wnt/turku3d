import * as THREE from 'three/webgpu';
import type { DriverLayout } from './layout';
import { TIERS } from './model';
import type { MotionPose } from './motion';
import { DriverRig, type DriverTemplate } from './rig';

/** Static drivers in the ambient traffic cars near the camera (README "Ambient traffic"): no IK, no motion, no wheel.
 *  Each posture class is the driver posed once at rest (steering straight) and skinned on the CPU into a plain
 *  geometry; every car of that class within AMBIENT_DISTANCE is one instance of it, moved from the class's H-point
 *  to its own model's. So the whole street costs one instanced draw per class in view, whatever the car count. */

export const AMBIENT_DISTANCE = 40;
const STILL: MotionPose = { pitch: 0, roll: 0, bump: 0, headYaw: 0, headPitch: 0, reverse: 0, horn: 0, gLon: 0, gLat: 0 };

/** A traffic car drawn this frame: its fleet model's driver layout and its model-frame matrix (fleet/visuals.ts). */
export interface AmbientSeat { layout: DriverLayout; matrix: THREE.Matrix4 }

interface Pose { mesh: THREE.InstancedMesh; hips: THREE.Vector3; count: number }

/** The driver's seated rest pose for `layout`, skinned into a static geometry in the model frame: the body, legs and
 *  face tiers (not the drawn wheel: the traffic batches draw the model's own). */
export function bakePose(template: DriverTemplate, layout: DriverLayout): THREE.BufferGeometry {
  const rig = new DriverRig(template), frame = new THREE.Group();
  frame.add(rig.model);
  rig.pose(frame, layout, STILL, 0);
  frame.updateMatrixWorld(true);
  const mesh = rig.mesh, shared = template.model.geometry, ends = template.model.tierEnds;
  const { bones, boneInverses } = mesh.skeleton;
  const bone = bones.map((b, i) => new THREE.Matrix4().multiplyMatrices(b.matrixWorld, boneInverses[i]).multiply(mesh.bindMatrix).elements);
  const position = shared.getAttribute('position'), normal = shared.getAttribute('normal');
  const joints = shared.getAttribute('skinIndex'), weights = shared.getAttribute('skinWeight'), index = shared.index!;
  // Every vertex the kept tiers use, renumbered.
  const keep = new Int32Array(position.count).fill(-1), used: number[] = [];
  const kept: number[] = [];
  for (let i = 0; i < ends[TIERS - 1]; i++) {
    if (i >= ends[0] && i < ends[1]) continue;
    const v = index.getX(i);
    if (keep[v] < 0) { keep[v] = used.length; used.push(v); }
    kept.push(keep[v]);
  }
  const n = used.length, p = new Float32Array(n * 3), nn = new Float32Array(n * 3), uv = new Float32Array(n * 2), rough = new Float32Array(n);
  const sourceUv = shared.getAttribute('uv'), sourceRough = shared.getAttribute('driverRoughness');
  const m = new Float64Array(16);
  used.forEach((v, o) => {
    m.fill(0);
    for (let k = 0; k < 4; k++) {
      const w = weights.getComponent(v, k); if (!w) continue;
      const e = bone[joints.getComponent(v, k)];
      for (let c = 0; c < 16; c++) m[c] += w * e[c];
    }
    const x = position.getX(v), y = position.getY(v), z = position.getZ(v);
    p[o * 3] = m[0] * x + m[4] * y + m[8] * z + m[12]; p[o * 3 + 1] = m[1] * x + m[5] * y + m[9] * z + m[13]; p[o * 3 + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
    const a = normal.getX(v), b = normal.getY(v), c = normal.getZ(v);
    const nx = m[0] * a + m[4] * b + m[8] * c, ny = m[1] * a + m[5] * b + m[9] * c, nz = m[2] * a + m[6] * b + m[10] * c, l = Math.hypot(nx, ny, nz) || 1;
    nn[o * 3] = nx / l; nn[o * 3 + 1] = ny / l; nn[o * 3 + 2] = nz / l;
    uv[o * 2] = sourceUv.getX(v); uv[o * 2 + 1] = sourceUv.getY(v); rough[o] = sourceRough.getX(v);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(p, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(nn, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geometry.setAttribute('driverRoughness', new THREE.BufferAttribute(rough, 1));
  geometry.setIndex(n > 65535 ? new THREE.Uint32BufferAttribute(kept, 1) : new THREE.Uint16BufferAttribute(kept, 1));
  geometry.computeBoundingSphere();
  return geometry;
}

export class AmbientDrivers {
  private readonly poses = new Map<string, Pose>();
  private readonly offset = new THREE.Matrix4();

  constructor(private readonly template: DriverTemplate, private readonly parent: THREE.Object3D,
    private readonly prepare: (model: THREE.Object3D) => Promise<void>) {}

  /** Seats a static driver in each of `seats` (this frame's traffic cars near the camera) and hides the rest. */
  update(seats: readonly AmbientSeat[], count = seats.length) {
    for (const pose of this.poses.values()) pose.count = 0;
    for (let i = 0; i < count; i++) {
      const { layout, matrix } = seats[i];
      const pose = this.pose(layout);
      if (pose.count === pose.mesh.instanceMatrix.count) this.grow(layout.cls, pose);
      // The class pose's H-point moved to this model's.
      this.offset.makeTranslation(layout.hips[0] - pose.hips.x, layout.hips[1] - pose.hips.y, layout.hips[2] - pose.hips.z);
      pose.mesh.setMatrixAt(pose.count++, this.offset.premultiply(matrix));
    }
    for (const pose of this.poses.values()) {
      pose.mesh.count = pose.count; pose.mesh.visible = pose.count > 0;
      if (pose.count) pose.mesh.instanceMatrix.needsUpdate = true;
    }
  }

  /** Drawn instances per posture class (the dev snapshot). */
  snapshot() { return Object.fromEntries([...this.poses].map(([cls, p]) => [cls, p.count])); }

  private pose(layout: DriverLayout): Pose {
    let pose = this.poses.get(layout.cls);
    if (!pose) {
      // The first model seen of a class sets its pose; the class's others differ from it by their H-point.
      const mesh = new THREE.InstancedMesh(bakePose(this.template, layout), this.template.model.material, 8);
      mesh.name = `ambient-drivers/${layout.cls}`;
      mesh.castShadow = false; mesh.receiveShadow = true;
      // Within AMBIENT_DISTANCE only; the instances' bounds change every frame.
      mesh.frustumCulled = false;
      pose = { mesh, hips: new THREE.Vector3(...layout.hips), count: 0 };
      this.poses.set(layout.cls, pose);
      this.parent.add(mesh);
      void this.prepare(mesh).catch(error => console.warn(`[drivers] ambient driver preparation failed: ${String(error)}`));
    }
    return pose;
  }

  private grow(cls: string, pose: Pose) {
    const old = pose.mesh, mesh = new THREE.InstancedMesh(old.geometry, old.material, old.instanceMatrix.count * 2);
    mesh.name = old.name; mesh.castShadow = false; mesh.receiveShadow = true; mesh.frustumCulled = false;
    (mesh.instanceMatrix.array as Float32Array).set(old.instanceMatrix.array as Float32Array);
    old.removeFromParent(); old.dispose();
    this.parent.add(mesh);
    pose.mesh = mesh;
    this.poses.set(cls, pose);
  }
}
