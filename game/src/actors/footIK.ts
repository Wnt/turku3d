import * as THREE from 'three/webgpu';
import type { RayHit } from '../physics/protocol';
import type { V3 } from '../core/origin';
export type FootRaycast = (origin: V3) => Promise<RayHit | null>;
const UP = new THREE.Vector3(0, 1, 0);
const v = () => new THREE.Vector3();

/** Rotate a bone in world space, while preserving its parent's transform. */
function aim(bone: THREE.Object3D, from: THREE.Vector3, to: THREE.Vector3) {
  const world = bone.getWorldQuaternion(new THREE.Quaternion());
  const turn = new THREE.Quaternion().setFromUnitVectors(from.clone().normalize(), to.clone().normalize());
  const parent = bone.parent!.getWorldQuaternion(new THREE.Quaternion());
  bone.quaternion.copy(parent.invert().multiply(turn.multiply(world)));
  bone.updateWorldMatrix(false, true);
}
/** Analytic two-bone solve. Knee pole follows character forward, lengths never change. */
export function solveLeg(hip: THREE.Object3D, knee: THREE.Object3D, ankle: THREE.Object3D, target: THREE.Vector3, pole: THREE.Vector3) {
  const h = hip.getWorldPosition(v()), k = knee.getWorldPosition(v()), a = ankle.getWorldPosition(v());
  const upper = h.distanceTo(k), lower = k.distanceTo(a);
  const dir = target.clone().sub(h);
  const distance = THREE.MathUtils.clamp(dir.length(), Math.abs(upper - lower) + 0.0001, upper + lower - 0.0001);
  dir.normalize();
  const bend = pole.clone().addScaledVector(dir, -pole.dot(dir)).normalize();
  const along = (upper * upper - lower * lower + distance * distance) / (2 * distance);
  const side = Math.sqrt(Math.max(0, upper * upper - along * along));
  const wantedKnee = h.clone().addScaledVector(dir, along).addScaledVector(bend, side);
  aim(hip, k.sub(h), wantedKnee.sub(h));
  const newKnee = knee.getWorldPosition(v());
  aim(knee, ankle.getWorldPosition(v()).sub(newKnee), target.clone().sub(newKnee));
}
interface Leg {
  hip: THREE.Object3D; knee: THREE.Object3D; ankle: THREE.Object3D;
  restRotation: THREE.Quaternion; clearance: number;
  hit: RayHit | null; queriedAt: THREE.Vector3; busy: boolean;
}

export class FootIK {
  private legs: Leg[];
  private hips: THREE.Object3D;
  private saved: { bone: THREE.Object3D; q: THREE.Quaternion; p: THREE.Vector3 }[] = [];
  private pelvis = 0;
  /** Diagnostics are actual solved ankle positions and ray targets, used by the route test. */
  contacts: { ankle: number[]; target: number[]; material: number; lift: number }[] = [];
  constructor(private root: THREE.Object3D, model: THREE.Object3D, private raycast: FootRaycast) {
    const bone = (name: string) => {
      const b = model.getObjectByName(THREE.PropertyBinding.sanitizeNodeName(name));
      if (!b) throw new Error(`IK bone missing: ${name}`);
      return b;
    };
    this.hips = bone('DEF-hips');
    root.updateWorldMatrix(true, true);
    this.legs = ['L', 'R'].map(side => {
      const ankle = bone(`DEF-foot.${side}`);
      return { hip: bone(`DEF-thigh.${side}`), knee: bone(`DEF-shin.${side}`), ankle,
        restRotation: ankle.getWorldQuaternion(new THREE.Quaternion()), clearance: ankle.getWorldPosition(v()).y - root.position.y,
        hit: null, queriedAt: v(), busy: false };
    });
  }
  /** Mixer tracks do not necessarily rewrite every transform; restore the unmodified animated pose first. */
  restore() {
    for (const s of this.saved) { s.bone.quaternion.copy(s.q); s.bone.position.copy(s.p); }
    this.saved = [];
  }
  update(dt: number, grounded: boolean, stairs: boolean) {
    this.contacts = [];
    if (!grounded) { this.pelvis = 0; for (const l of this.legs) l.hit = null; return; }
    this.root.updateWorldMatrix(true, true);
    const animated = this.legs.map(l => l.ankle.getWorldPosition(v()));
    for (let i = 0; i < this.legs.length; i++) {
      const l = this.legs[i], p = animated[i];
      if (!l.busy) {
        l.busy = true;
        const origin = { x: p.x, y: this.root.position.y + 0.65, z: p.z };
        void this.raycast(origin).then(hit => { l.hit = hit; l.queriedAt.set(origin.x, origin.y, origin.z); })
          .finally(() => { l.busy = false; });
      }
      // Do not use a result from the other side of a teleport or a stalled worker.
      if (Math.hypot(p.x - l.queriedAt.x, p.z - l.queriedAt.z) > 0.3) l.hit = null;
    }
    const targets = this.legs.map((l, i) => {
      if (!l.hit || l.hit.normal.y < 0.5) return null;
      const target = animated[i].clone();
      const lift = Math.max(0, target.y - this.root.position.y - l.clearance);
      // Preserve the swing arc and add toe clearance on stairs; stance feet settle on the discrete treads.
      target.y = l.hit.point.y + l.clearance + lift + (stairs ? Math.min(0.09, lift * 0.6) : 0);
      return { target, lift };
    });
    const offsets = this.legs.map(l => l.hit ? l.hit.point.y - this.root.position.y : 0);
    // Terrain height alone misses an extended trailing leg during descent. Constrain the pelvis by
    // each leg's actual reach in its animated pose, keeping 15 mm of knee flexion in reserve.
    let reachCeiling = Infinity;
    for (let i = 0; i < this.legs.length; i++) {
      const t = targets[i];
      if (!t) continue;
      const l = this.legs[i], hip = l.hip.getWorldPosition(v()), knee = l.knee.getWorldPosition(v());
      const reach = hip.distanceTo(knee) + knee.distanceTo(animated[i]) - 0.015;
      const horizontal = Math.hypot(hip.x - t.target.x, hip.z - t.target.z);
      const vertical = Math.sqrt(Math.max(0, reach * reach - horizontal * horizontal));
      reachCeiling = Math.min(reachCeiling, t.target.y + vertical - hip.y);
    }
    const desiredPelvis = THREE.MathUtils.clamp(Math.min(Math.min(...offsets) - 0.015, reachCeiling), -0.28, 0.12);
    this.pelvis += (desiredPelvis - this.pelvis) * (1 - Math.exp(-dt * 18));
    // Keep stance feet reachable immediately; recovery upward stays damped.
    this.pelvis = Math.min(this.pelvis, Math.max(-0.28, reachCeiling));
    const bones = [this.hips, ...this.legs.flatMap(l => [l.hip, l.knee, l.ankle])];
    this.saved = bones.map(bone => ({ bone, q: bone.quaternion.clone(), p: bone.position.clone() }));
    // Convert world-up pelvis displacement into the rig's local axes (UAL's hips parent is rotated).
    const localOffset = UP.clone().multiplyScalar(this.pelvis).applyQuaternion(this.hips.parent!.getWorldQuaternion(new THREE.Quaternion()).invert());
    this.hips.position.add(localOffset);
    this.root.updateWorldMatrix(true, true);
    const yaw = this.root.getWorldQuaternion(new THREE.Quaternion());
    const pole = new THREE.Vector3(0, 0, 1).applyQuaternion(yaw);
    for (let i = 0; i < this.legs.length; i++) {
      const l = this.legs[i], hit = l.hit, t = targets[i];
      if (!hit || !t) continue;
      const { target, lift } = t;
      solveLeg(l.hip, l.knee, l.ankle, target, pole);
      const alignment = new THREE.Quaternion().setFromUnitVectors(UP, new THREE.Vector3(hit.normal.x, hit.normal.y, hit.normal.z).normalize());
      const desired = alignment.multiply(yaw.clone().multiply(l.restRotation));
      l.ankle.quaternion.copy(l.ankle.parent!.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(desired));
      l.ankle.updateWorldMatrix(false, true);
      this.contacts.push({ ankle: l.ankle.getWorldPosition(v()).toArray(), target: target.toArray(), material: hit.material, lift });
    }
  }
}
