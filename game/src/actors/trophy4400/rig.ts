import * as T from 'three/webgpu';
import type { WheelState } from '../../physics/protocol';
import { TRUCK, TRUCK_STATIC_LENGTH } from '../../physics/constants';
import { CORNERS, TruckLinkage, type Quat, type Vec } from './linkage';
import type { TrophyAsset } from './asset';

/** Binds the linkage solver (linkage.ts) to one truck model's bones: the local truck, extra draft trucks and every
 *  remote clone get their own rig (WeakMap per root, like the remote cars), driven only by `WheelState[]`, so a
 *  remote truck articulates from the synced per-wheel steer and hub height exactly like the sender's. */

/** The GLB ground origin in the chassis frame: hub static height below the wheel connection, then the tyre radius. */
export const GROUND_Y = TRUCK.wheels[0].y - TRUCK_STATIC_LENGTH - TRUCK.wheelRadius;
/** Steering-wheel turns per front steer (±42° front lock gives about ±270° at the wheel). */
const STEERING_RATIO = 6.4;
/** Solve again only when a hub moved or a steer changed by more than this (m, rad); parked trucks solve once. */
const EPSILON = 5e-4;

type Wheel = Pick<WheelState, 'pos' | 'steer' | 'rotation'>;
const Z = new T.Vector3(0, 0, 1), Y = new T.Vector3(0, 1, 0);
const q1 = new T.Quaternion(), q2 = new T.Quaternion(), v1 = new T.Vector3();
const quat = (q: Quat) => q1.set(q[0], q[1], q[2], q[3]);

class TrophyRig {
  private readonly bones = new Map<string, T.Object3D>();
  private readonly linkage: TruckLinkage;
  private readonly previous = new Float64Array(8).fill(NaN);
  private readonly markers: T.Object3D[];
  private readonly inputs = CORNERS.map(() => ({ dy: 0, steer: 0 }));
  solves = 0;

  constructor(root: T.Object3D, private readonly asset: TrophyAsset) {
    root.traverse(o => { if (asset.rest.has(o.name) && (o as T.Bone).isBone) this.bones.set(o.name, o); });
    this.linkage = new TruckLinkage(asset.hardpoints, asset.hardpoints.springs);
    this.markers = TRUCK.wheels.map((_, i) => root.getObjectByName(`vehicle-wheel-${i}`)!).filter(Boolean);
  }

  /** Sets a bone to its rest transform composed with a world-delta rotation, an absolute position and a Y scale. */
  private set(name: string, p?: Vec | null, delta?: Quat | null, scaleY = 1) {
    const bone = this.bones.get(name), rest = this.asset.rest.get(name);
    if (!bone || !rest) return;
    if (p) bone.position.set(p[0], p[1], p[2]); else bone.position.copy(rest.p);
    if (delta) bone.quaternion.multiplyQuaternions(quat(delta), rest.q); else bone.quaternion.copy(rest.q);
    bone.scale.set(rest.s.x, rest.s.y * scaleY, rest.s.z);
  }

  update(wheels: readonly Wheel[]) {
    let changed = false;
    CORNERS.forEach((c, i) => {
      const w = wheels[i];
      if (!w) return;
      const dy = w.pos.y - GROUND_Y - this.asset.hardpoints.corners[c].hub[1];
      if (!(Math.abs(dy - this.previous[i * 2]) <= EPSILON) || !(Math.abs(w.steer - this.previous[i * 2 + 1]) <= EPSILON)) {
        changed = true; this.previous[i * 2] = dy; this.previous[i * 2 + 1] = w.steer;
      }
      this.inputs[i].dy = this.previous[i * 2]; this.inputs[i].steer = this.previous[i * 2 + 1];
    });
    if (changed) this.solve();
    // Spin every frame: forward rolling is a rotation about −Z (SPEC §2.2) for both sides.
    CORNERS.forEach((c, i) => {
      const bone = this.bones.get(`wheel-${c}`), rest = this.asset.rest.get(`wheel-${c}`);
      if (!bone || !rest || !wheels[i]) return;
      bone.quaternion.copy(rest.q).multiply(q2.setFromAxisAngle(Z, -wheels[i].rotation));
      const marker = this.markers[i], upright = this.bones.get(`upright-${c}`);
      if (marker && upright) {
        marker.position.copy(upright.position).add(v1.set(0, GROUND_Y, 0));
        marker.quaternion.copy(upright.quaternion).multiply(bone.quaternion);
      }
    });
  }

  private solve() {
    this.solves++;
    const pose = this.linkage.solve(this.inputs);
    for (const c of CORNERS) {
      const s = pose.corners[c];
      this.set(`lca-${c}`, null, s.lca);
      this.set(`uca-${c}`, null, s.uca);
      this.set(`upright-${c}`, s.upright.p, s.upright.q);
      this.set(`tierod-${c}`, s.tierod.p, s.tierod.q, s.tierod.scale);
      this.set(`coil-${c}-body`, null, s.coil.body);
      this.set(`coil-${c}-shaft`, s.coil.shaft.p, s.coil.shaft.q);
      this.set(`coil-${c}-spring`, s.coil.spring.p, s.coil.spring.q, s.coil.spring.scale);
      this.set(`bypass-${c}-body`, null, s.bypass.body);
      this.set(`bypass-${c}-shaft`, s.bypass.shaft.p, s.bypass.shaft.q);
      this.set(`cv-${c}-inner`, null, s.cv.inner);
      this.set(`cv-${c}-shaft`, null, s.cv.shaft.q, s.cv.shaft.scale);
      this.set(`cv-${c}-outer`, s.cv.outer.p, s.cv.outer.q);
    }
    for (const [name, offset] of [['rack-front-slider', pose.rack.front], ['rack-rear-slider', pose.rack.rear]] as const) {
      const rest = this.asset.rest.get(name)!;
      this.set(name, [rest.p.x, rest.p.y, rest.p.z + offset]);
    }
    const wheel = this.bones.get('steer-wheel'), rest = this.asset.rest.get('steer-wheel');
    if (wheel && rest) wheel.quaternion.copy(rest.q).multiply(q2.setFromAxisAngle(Y, STEERING_RATIO * (this.inputs[0].steer + this.inputs[1].steer) / 2));
  }
}

const rigs = new WeakMap<T.Object3D, TrophyRig>();
let current: TrophyAsset | null = null;
/** Called once the asset has loaded; roots without its bones are ignored. */
export function useTrophyAsset(asset: TrophyAsset) { current = asset; }

/** Poses the truck model under `root` (its own TruckVisual root or a remote clone of it) from the wheel states. */
export function applyTrophyRig(root: T.Object3D, wheels: readonly Wheel[]) {
  let rig = rigs.get(root);
  if (!rig) {
    if (!current || !root.getObjectByName('upright-fl')) return;
    rig = new TrophyRig(root, current); rigs.set(root, rig);
  }
  rig.update(wheels);
}

/** How often this root's linkage was solved (tests and the lab). */
export const trophyRigSolves = (root: T.Object3D) => rigs.get(root)?.solves ?? 0;

/** Wheel states of the static ride (all hubs at the static height, no steer), in the chassis frame. */
export function staticWheels(): Wheel[] {
  return TRUCK.wheels.map(at => ({ pos: { x: at.x, y: at.y - TRUCK_STATIC_LENGTH, z: at.z }, steer: 0, rotation: 0 }));
}
