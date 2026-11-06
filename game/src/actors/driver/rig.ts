import * as THREE from 'three/webgpu';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';
import { BODY, grip, type DriverLayout, type V3 } from './layout';
import type { MotionPose } from './motion';

/** The seated driver's skeleton pose: the UAL `Driving_Loop` clip as the base (legs, fingers' grip), the hips on the
 *  layout's H-point plus the seat bounce, recline and g-lean over the three spine bones (0.4 / 0.35 / 0.25), head look,
 *  whiplash and counter-roll on neck and head, and two-bone arm IK to grip points on the steering-wheel rim that turn
 *  with it (hands never leave the rim; the arms cross at lock). Every rotation is applied about the joint in the seat
 *  frame (x left, y up, z forward), so bone roll conventions do not matter. */

const SPINE = ['DEF-spine001', 'DEF-spine002', 'DEF-spine003'] as const;
const SPLIT = [0.4, 0.35, 0.25];
const DRIVEN = ['DEF-hips', ...SPINE, 'DEF-neck', 'DEF-head', 'DEF-shoulderL', 'DEF-upper_armL', 'DEF-forearmL', 'DEF-handL',
  'DEF-shoulderR', 'DEF-upper_armR', 'DEF-forearmR', 'DEF-handR'] as const;
type Driven = typeof DRIVEN[number];
/** Never visible inside a car (and teeth are 7k triangles); face details only near the camera. */
const HIDDEN_MATERIALS = new Set(['Teeth', 'Tongue', 'Cloth_shoes']);
export const FACE_MATERIALS = new Set(['Eye', 'Iris', 'Cornea', 'Eyebrow', 'Eyelash']);
/** Clavicle swing allowed to reach a far grip (the crossing arm at lock). */
const CLAVICLE = 0.35;

interface Rest { p: THREE.Vector3; q: THREE.Quaternion; s: THREE.Vector3 }

/** Built once from the loaded player character (actors/player.ts): its scene, the base pose and the frames the rig
 *  needs. Drivers are SkeletonUtils clones: geometry, materials (so programs) shared, bones per driver. */
export class DriverTemplate {
  readonly rest = new Map<string, Rest>();
  /** Model-space orientation of each driven bone's parent in the base pose. */
  readonly parents = new Map<Driven, THREE.Quaternion>();
  /** The hips joint in model space in the base pose. */
  readonly hips = new THREE.Vector3();

  constructor(readonly scene: THREE.Object3D, clips: readonly THREE.AnimationClip[]) {
    const clip = clips.find(c => c.name === 'Driving_Loop');
    if (!clip) throw new Error('character clip Driving_Loop missing');
    const model = cloneSkeleton(scene);
    model.position.set(0, 0, 0); model.quaternion.identity(); model.scale.set(1, 1, 1);
    const mixer = new THREE.AnimationMixer(model);
    mixer.clipAction(clip).play();
    mixer.update(0);
    model.updateMatrixWorld(true);
    model.traverse(o => { if ((o as THREE.Bone).isBone) this.rest.set(o.name, { p: o.position.clone(), q: o.quaternion.clone(), s: o.scale.clone() }); });
    for (const name of DRIVEN) {
      const bone = model.getObjectByName(name);
      if (!bone?.parent) throw new Error(`character bone ${name} missing`);
      this.parents.set(name, bone.parent.getWorldQuaternion(new THREE.Quaternion()));
    }
    model.getObjectByName('DEF-hips')!.getWorldPosition(this.hips);
    mixer.stopAllAction(); mixer.uncacheRoot(model);
  }
}

const tmp = { a: new THREE.Vector3(), b: new THREE.Vector3(), c: new THREE.Vector3(), d: new THREE.Vector3(), e: new THREE.Vector3(),
  q: new THREE.Quaternion(), r: new THREE.Quaternion(), s: new THREE.Quaternion(), m: new THREE.Matrix4(), euler: new THREE.Euler(0, 0, 0, 'YXZ') };
const v3 = (v: V3, out: THREE.Vector3) => out.set(v[0], v[1], v[2]);

export class DriverRig {
  /** The character clone; a child of the seat frame. */
  readonly model: THREE.Object3D;
  private readonly bones = new Map<Driven, THREE.Object3D>();
  readonly face: THREE.Mesh[] = [];
  /** Hand-to-grip error of the last pose (m), for tests and the dev snapshot. */
  readonly reach = { left: 0, right: 0 };

  constructor(private readonly template: DriverTemplate) {
    this.model = cloneSkeleton(template.scene);
    this.model.name = 'driver';
    this.model.position.set(0, 0, 0); this.model.quaternion.identity();
    this.model.traverse(o => {
      const rest = template.rest.get(o.name);
      if (rest && (o as THREE.Bone).isBone) { o.position.copy(rest.p); o.quaternion.copy(rest.q); o.scale.copy(rest.s); }
      const mesh = o as THREE.SkinnedMesh;
      if (!mesh.isMesh) return;
      const material = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as THREE.Material;
      // Inside the car body's shadow; a fixed sphere over the seated pose and the reach (no CPU skinning for culling).
      mesh.castShadow = false; mesh.frustumCulled = true;
      mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0.75, -0.05), 0.95);
      mesh.boundingBox = new THREE.Box3(new THREE.Vector3(-0.7, -0.1, -0.7), new THREE.Vector3(0.7, 1.6, 0.7));
      if (HIDDEN_MATERIALS.has(material.name)) mesh.visible = false;
      if (FACE_MATERIALS.has(material.name)) this.face.push(mesh);
    });
    for (const name of DRIVEN) this.bones.set(name, this.model.getObjectByName(name)!);
  }

  private reset() {
    for (const [name, bone] of this.bones) {
      const rest = this.template.rest.get(name)!;
      bone.position.copy(rest.p); bone.quaternion.copy(rest.q);
    }
  }

  /** Rotates `name` about its joint by the seat-frame rotation `r` (applied on top of its current local rotation). */
  private turn(name: Driven, r: THREE.Quaternion) {
    const bone = this.bones.get(name)!, parent = this.template.parents.get(name)!;
    // local' = P^-1 r P local, with P the parent's base orientation in the model (= seat) frame.
    tmp.s.copy(parent).invert().multiply(r).multiply(parent);
    bone.quaternion.premultiply(tmp.s);
  }

  /** Seat-frame rotation: yaw about +Y (left), pitch about +X (forward), roll about +Z (toward the driver's right). */
  private static rotation(yaw: number, pitch: number, roll: number, out: THREE.Quaternion) {
    tmp.euler.set(pitch, yaw, roll, 'YXZ');
    return out.setFromEuler(tmp.euler);
  }

  /** Poses the driver in `frame` (the seat frame, already placed in the world) for the wheel angle `wheel` (rad). */
  pose(frame: THREE.Object3D, layout: DriverLayout, motion: MotionPose, wheel: number) {
    this.reset();
    const t = this.template;
    this.model.position.set(layout.hips[0] - t.hips.x, layout.hips[1] - t.hips.y + motion.bump, layout.hips[2] - t.hips.z);
    // Torso: the class recline at the lumbar joint, then lean (and the reverse look's upper-body turn) over the spine.
    const recline = layout.recline - BODY.recline;
    const back = motion.reverse;
    for (let i = 0; i < 3; i++) {
      const pitch = SPLIT[i] * motion.pitch - (i === 0 ? recline : 0);
      const yaw = i === 2 ? -back * 20 * Math.PI / 180 : 0;
      this.turn(SPINE[i], DriverRig.rotation(yaw, pitch, SPLIT[i] * motion.roll, tmp.q));
    }
    // Head: level the eyes against the recline, look, nod and whiplash pitch; half the spine roll counter-rolled.
    const headYaw = motion.headYaw - back * 70 * Math.PI / 180;
    this.turn('DEF-neck', DriverRig.rotation(0.4 * headYaw, 0.5 * recline + 0.4 * motion.headPitch, -0.25 * motion.roll, tmp.q));
    this.turn('DEF-head', DriverRig.rotation(0.6 * headYaw, 0.5 * recline + 0.6 * motion.headPitch, -0.25 * motion.roll, tmp.q));
    frame.updateWorldMatrix(true, false);
    this.model.updateMatrixWorld(true);
    const world = frame.matrixWorld;
    for (const side of [1, -1] as const) {
      const suffix = side > 0 ? 'L' : 'R';
      const g = grip(layout.wheel, side * layout.wheel.grip + wheel);
      const target = v3(g.wrist, tmp.a).applyMatrix4(world), point = v3(g.point, tmp.b).applyMatrix4(world);
      if (side < 0 && back > 0) {
        // Reversing: the passenger-side hand leaves the rim for the passenger seat's backrest.
        const rest = v3([layout.hips[0] - 0.62, layout.hips[1] + 0.42, layout.hips[2] - 0.08], tmp.c).applyMatrix4(world);
        const w = Math.min(1, back * 1.4);
        target.lerp(rest, w); point.lerp(tmp.d.copy(rest).add(tmp.e.set(0, -0.06, 0)), w);
      }
      const pole = tmp.c.set(side * 0.55, -1, -0.25).transformDirection(world);
      const error = this.arm(suffix, target, point, pole);
      if (side > 0) this.reach.left = error; else this.reach.right = error;
    }
  }

  /** Two-bone IK of one arm to the wrist `target` (world), elbow toward `pole`, hand pointed at the grip `point`;
   *  the clavicle swings toward a target beyond reach. Returns the remaining wrist error (m). */
  private arm(side: 'L' | 'R', target: THREE.Vector3, point: THREE.Vector3, pole: THREE.Vector3): number {
    const clavicle = this.bones.get(`DEF-shoulder${side}`)!, upper = this.bones.get(`DEF-upper_arm${side}`)!;
    const fore = this.bones.get(`DEF-forearm${side}`)!, hand = this.bones.get(`DEF-hand${side}`)!;
    const shoulder = upper.getWorldPosition(new THREE.Vector3()), elbow = fore.getWorldPosition(new THREE.Vector3());
    const wrist = hand.getWorldPosition(new THREE.Vector3());
    const a = shoulder.distanceTo(elbow), b = elbow.distanceTo(wrist);
    let d = shoulder.distanceTo(target);
    const limit = (a + b) * 0.995;
    if (d > limit) {
      // Swing the clavicle about its joint toward the target, by the angle that closes the gap (at most CLAVICLE).
      const root = clavicle.getWorldPosition(tmp.c), arm = shoulder.distanceTo(root);
      tmp.r.setFromUnitVectors(tmp.d.subVectors(shoulder, root).normalize(), tmp.e.subVectors(target, root).normalize());
      const full = 2 * Math.acos(Math.min(1, Math.abs(tmp.r.w)));
      if (full > 1e-4 && arm > 1e-3) {
        tmp.r.slerp(tmp.s.identity(), 1 - Math.min(1, Math.min(CLAVICLE, 1.2 * (d - limit) / arm) / full));
        this.rotateWorld(clavicle, tmp.r);
        upper.getWorldPosition(shoulder); fore.getWorldPosition(elbow); hand.getWorldPosition(wrist);
        d = shoulder.distanceTo(target);
      }
    }
    const reach = Math.min(Math.max(d, Math.abs(a - b) + 1e-3), (a + b) * 0.9999);
    const dir = tmp.d.subVectors(target, shoulder).normalize();
    const cos = (a * a + reach * reach - b * b) / (2 * a * reach), sin = Math.sqrt(Math.max(0, 1 - cos * cos));
    const bend = tmp.e.copy(pole).addScaledVector(dir, -pole.dot(dir));
    if (bend.lengthSq() < 1e-8) bend.set(0, -1, 0).addScaledVector(dir, -dir.y);
    bend.normalize();
    const elbowTarget = new THREE.Vector3().copy(shoulder).addScaledVector(dir, a * cos).addScaledVector(bend, a * sin);
    this.aim(upper, shoulder, elbow, elbowTarget);
    fore.getWorldPosition(elbow); hand.getWorldPosition(wrist);
    this.aim(fore, elbow, wrist, target);
    hand.getWorldPosition(wrist);
    // The hand points from the wrist at the grip; its roll stays the clip's grip.
    const tip = tmp.d.set(0, 1, 0).applyQuaternion(hand.getWorldQuaternion(tmp.q)).add(wrist);
    this.aim(hand, wrist, tip, point);
    return wrist.distanceTo(target);
  }

  /** Rotates `bone` about its joint so that `child` (world) turns toward `goal` (world). */
  private aim(bone: THREE.Object3D, joint: THREE.Vector3, child: THREE.Vector3, goal: THREE.Vector3) {
    const from = new THREE.Vector3().subVectors(child, joint), to = new THREE.Vector3().subVectors(goal, joint);
    if (from.lengthSq() < 1e-10 || to.lengthSq() < 1e-10) return;
    this.rotateWorld(bone, tmp.r.setFromUnitVectors(from.normalize(), to.normalize()));
  }

  /** Applies the world rotation `r` to `bone` about its joint and refreshes its subtree's world matrices. */
  private rotateWorld(bone: THREE.Object3D, r: THREE.Quaternion) {
    const parent = bone.parent!.getWorldQuaternion(new THREE.Quaternion());
    const local = new THREE.Quaternion().copy(parent).invert().multiply(r).multiply(parent);
    bone.quaternion.premultiply(local);
    bone.updateMatrixWorld(true);
  }

  /** Face meshes are drawn near the camera only. */
  showFace(visible: boolean) { for (const mesh of this.face) mesh.visible = visible; }
}

