import * as THREE from 'three/webgpu';
import type { RayHit } from '../physics/protocol';
import type { V3 } from '../core/origin';
import { FootPlant } from './footPlant';
import { soleMarkers, type SoleMarker } from './soleMarkers';
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
  clearance: number; markers: SoleMarker[]; plant: FootPlant; marker: number;
  hit: RayHit | null; hits: (RayHit | null)[]; queriedAt: THREE.Vector3; busy: boolean;
}

export class FootIK {
  private legs: Leg[];
  private hips: THREE.Object3D;
  private saved: { bone: THREE.Object3D; q: THREE.Quaternion; p: THREE.Vector3 }[] = [];
  private pelvis = 0;
  private epoch = 0;
  private previousRoot = new THREE.Vector3(Infinity, Infinity, Infinity);
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
        clearance: ankle.getWorldPosition(v()).y - root.position.y, markers: [], plant: new FootPlant(), marker: -1,
        hit: null, hits: [], queriedAt: v(), busy: false };
    });
    const markers = soleMarkers(model, this.legs.map(l => l.ankle));
    this.legs.forEach((leg, i) => { leg.markers = markers[i]; });
  }
  /** Mixer tracks do not necessarily rewrite every transform; restore the unmodified animated pose first. */
  restore() {
    for (const s of this.saved) { s.bone.quaternion.copy(s.q); s.bone.position.copy(s.p); }
    this.saved = [];
  }
  update(dt: number, grounded: boolean, stairs: boolean, strideScale = 1, bodyHeight?: number) {
    this.contacts = [];
    const teleported = this.previousRoot.distanceTo(this.root.position) > 1;
    this.previousRoot.copy(this.root.position);
    if (!grounded || teleported) {
      this.epoch++; this.pelvis = 0;
      for (const l of this.legs) { l.hit = null; l.hits = []; l.plant.reset(); l.marker = -1; }
      if (!grounded) return;
    }
    this.root.updateWorldMatrix(true, true);
    const animated = this.legs.map(l => l.ankle.getWorldPosition(v()));
    const animatedRotations = this.legs.map(l => l.ankle.getWorldQuaternion(new THREE.Quaternion()));
    const yaw = this.root.getWorldQuaternion(new THREE.Quaternion());
    const pole = new THREE.Vector3(0, 0, 1).applyQuaternion(yaw);
    const animatedMarkers = this.legs.map(l => l.markers.map(m =>
      m.mesh.getVertexPosition(m.vertex, v()).applyMatrix4(m.mesh.matrixWorld)));
    for (let i = 0; i < this.legs.length; i++) {
      const l = this.legs[i], p = animated[i].clone();
      const forward = p.clone().sub(this.root.position).dot(pole);
      p.addScaledVector(pole, forward * (strideScale - 1));
      if (!l.busy) {
        l.busy = true;
        const epoch = this.epoch;
        const y = this.root.position.y + 0.65;
        const markers = animatedMarkers[i].length ? animatedMarkers[i] : [animated[i]];
        if (l.marker >= 0 && l.marker < markers.length) {
          const pivot = markers[l.marker].clone().sub(animated[i]);
          const planted = l.plant.project(p.clone().add(pivot));
          p.x = planted.x - pivot.x; p.z = planted.z - pivot.z;
        }
        const margins = stairs ? [-0.05, 0, 0.05] : [0];
        const origins = markers.flatMap(marker => {
          const offset = marker.clone().sub(animated[i]);
          return margins.map(margin => ({ x: p.x + offset.x + pole.x * margin, y,
            z: p.z + offset.z + pole.z * margin }));
        });
        // A tiny footprint band catches both ascending and descending tread
        // edges during the asynchronous query; ordinary ground needs one probe.
        if (!stairs) {
          const toe = Math.max(...markers.map(marker => marker.clone().sub(animated[i]).dot(pole)));
          origins.push({ x: p.x + pole.x * (toe + 0.06), y, z: p.z + pole.z * (toe + 0.06) });
        }
        void Promise.all(origins.map(origin => this.raycast(origin))).then(hits => {
          l.busy = false;
          if (epoch !== this.epoch) return;
          const highest = (values: (RayHit | null)[]) => values.reduce<RayHit | null>((best, hit) =>
            hit && hit.normal.y >= 0.5 && (!best || hit.point.y > best.point.y) ? hit : best, null);
          l.hits = markers.map((_, k) => highest(hits.slice(k * margins.length, (k + 1) * margins.length)));
          if (!stairs) l.hits.push(hits.at(-1) ?? null);
          l.hit = highest(l.hits);
          l.queriedAt.set(p.x, y, p.z);
        }, () => { l.busy = false; if (epoch === this.epoch) { l.hit = null; l.hits = []; } });
      }
      // Do not use a result from the other side of a teleport or a stalled worker.
      if (Math.hypot(p.x - l.queriedAt.x, p.z - l.queriedAt.z) > 0.3
        || Math.abs(this.root.position.y + 0.65 - l.queriedAt.y) > 0.6) l.hit = null;
    }
    const targets = this.legs.map((l, i) => {
      if (!l.hit || l.hit.normal.y < 0.5) { l.plant.reset(); return null; }
      const target = animated[i].clone();
      const markers = animatedMarkers[i];
      const soleY = markers.length ? Math.min(...markers.map(p => p.y)) : target.y - l.clearance;
      const lift = Math.max(0, soleY - this.root.position.y);
      // Shorten a running stride without shortening bones or changing the authored swing height.
      const forward = (target.x - this.root.position.x) * pole.x + (target.z - this.root.position.z) * pole.z;
      target.addScaledVector(pole, forward * (strideScale - 1));
      const stance = 1 - THREE.MathUtils.smoothstep(lift, 0.025, 0.12);
      const alignment = new THREE.Quaternion().setFromUnitVectors(UP, new THREE.Vector3(l.hit.normal.x, l.hit.normal.y, l.hit.normal.z).normalize());
      const correction = new THREE.Quaternion().slerp(alignment, stance);
      const rotation = correction.clone().multiply(animatedRotations[i]);
      // Retain animated heel/toe roll, then lift the ankle just enough to put the
      // lowest transformed sole marker on the queried contact plane.
      const support = (markers.length ? markers : [animated[i].clone().addScaledVector(UP, -l.clearance)])
        .map(marker => marker.sub(animated[i]).applyQuaternion(correction));
      const heightAt = (offset: THREE.Vector3, k: number) => {
        let height = -Infinity;
        for (const hit of [l.hits[k], l.hits[support.length]]) {
          if (!hit || hit.normal.y < 0.5) continue;
          height = Math.max(height, hit.point.y - (hit.normal.x * (target.x + offset.x - hit.point.x)
            + hit.normal.z * (target.z + offset.z - hit.point.z)) / hit.normal.y - offset.y);
        }
        return height;
      };
      let lowest = 0;
      for (let k = 1; k < support.length; k++) if (heightAt(support[k], k) > heightAt(support[lowest], lowest)) lowest = k;
      // Hold the actual heel/toe support, not the ankle: the ankle must travel
      // around that pivot as the foot rolls. Hysteresis avoids corner chatter.
      if (l.marker < 0 || heightAt(support[lowest], lowest) > heightAt(support[l.marker], l.marker) + 0.015) {
        l.marker = lowest; l.plant.reset();
      }
      const unplantedX = target.x, unplantedZ = target.z;
      const pivot = support[l.marker], planted = l.plant.update(dt, target.clone().add(pivot), lift, true);
      target.x = planted.x - pivot.x; target.z = planted.z - pivot.z;
      target.y = -Infinity;
      for (let k = 0; k < support.length; k++) target.y = Math.max(target.y, heightAt(support[k], k));
      const swing = lift * THREE.MathUtils.smoothstep(lift, 0.015, 0.06);
      target.y += swing * Math.min(1, strideScale) + (stairs ? Math.min(0.09, swing * 0.6) : 0);
      const hip = l.hip.getWorldPosition(v());
      const knee = l.knee.getWorldPosition(v());
      const reach = hip.distanceTo(knee) + knee.distanceTo(animated[i]) - 0.015;
      // A trailing contact must release before it drags the whole body downward.
      // Terrain can still lower the pelvis; the world-space stance lock cannot.
      if (planted.weight > 0 && hip.clone().addScaledVector(UP, -0.045).distanceTo(target) > reach) {
        l.plant.reset(); target.x = unplantedX; target.z = unplantedZ;
        target.y = Math.max(...support.map(heightAt)) + swing * Math.min(1, strideScale)
          + (stairs ? Math.min(0.09, swing * 0.6) : 0);
      }
      const axis = animated[i].clone().sub(hip).normalize();
      const bend = l.knee.getWorldPosition(v()).sub(hip);
      bend.addScaledVector(axis, -bend.dot(axis));
      if (bend.lengthSq() < 1e-8) bend.copy(pole);
      bend.normalize();
      return { target, lift, rotation, bend, reach };
    });
    const bones = [this.hips, ...this.legs.flatMap(l => [l.hip, l.knee, l.ankle])];
    this.saved = bones.map(bone => ({ bone, q: bone.quaternion.clone(), p: bone.position.clone() }));
    // Keep the already evaluated foot targets while reducing authored running hip bob.
    // The rig parent is rotated, so this displacement must be transformed from world up.
    if (bodyHeight !== undefined) {
      const delta = bodyHeight - (this.hips.getWorldPosition(v()).y - this.root.position.y);
      this.hips.position.add(UP.clone().multiplyScalar(delta)
        .applyQuaternion(this.hips.parent!.getWorldQuaternion(new THREE.Quaternion()).invert()));
      this.root.updateWorldMatrix(true, true);
    }
    const offsets = this.legs.map(l => l.hit ? l.hit.point.y - this.root.position.y : 0);
    // Terrain height alone misses an extended trailing leg during descent. Constrain the pelvis by
    // each leg's actual reach in its animated pose, keeping 15 mm of knee flexion in reserve.
    let reachCeiling = Infinity;
    for (let i = 0; i < this.legs.length; i++) {
      const t = targets[i];
      if (!t) continue;
      const l = this.legs[i], hip = l.hip.getWorldPosition(v()), reach = t.reach;
      const horizontal = Math.hypot(hip.x - t.target.x, hip.z - t.target.z);
      const vertical = Math.sqrt(Math.max(0, reach * reach - horizontal * horizontal));
      reachCeiling = Math.min(reachCeiling, t.target.y + vertical - hip.y);
    }
    const desiredPelvis = THREE.MathUtils.clamp(Math.min(Math.min(...offsets) - 0.015, reachCeiling), -0.28, 0.12);
    this.pelvis += (desiredPelvis - this.pelvis) * (1 - Math.exp(-dt * 18));
    // Keep stance feet reachable immediately; recovery upward stays damped.
    this.pelvis = Math.min(this.pelvis, Math.max(-0.28, reachCeiling));
    // Convert world-up pelvis displacement into the rig's local axes (UAL's hips parent is rotated).
    const localOffset = UP.clone().multiplyScalar(this.pelvis).applyQuaternion(this.hips.parent!.getWorldQuaternion(new THREE.Quaternion()).invert());
    this.hips.position.add(localOffset);
    this.root.updateWorldMatrix(true, true);
    for (let i = 0; i < this.legs.length; i++) {
      const l = this.legs[i], hit = l.hit, t = targets[i];
      if (!hit || !t) continue;
      const { target, lift } = t;
      solveLeg(l.hip, l.knee, l.ankle, target, t.bend);
      l.ankle.quaternion.copy(l.ankle.parent!.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(t.rotation));
      l.ankle.updateWorldMatrix(false, true);
      this.contacts.push({ ankle: l.ankle.getWorldPosition(v()).toArray(), target: target.toArray(), material: hit.material, lift });
    }
  }
}
