import * as T from 'three/webgpu';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import { nightUniforms } from '../render/nightLights';
import { VEHICLE_BEAM_LIGHTS } from '../render/vehicleLightData';

/** The cars whose headlights light the road: the driven one and the nearest REMOTE_BEAM_CARS lit remote players' cars
 *  within REMOTE_BEAM_M (Jonni, city.lab 2026-11-09: remote players' lights and long beams must act as one's own).
 *  Two shadowless spotlights per car, all created at startup: a changed light set recompiles every lit material, so
 *  only their intensities change; render/vehicleLightData.ts packs the lit ones (VEHICLE_BEAM_LIGHTS). */
export const REMOTE_BEAM_CARS = VEHICLE_BEAM_LIGHTS / 2 - 1, REMOTE_BEAM_M = 150;
/** What a car's beams need: its chassis pose (physics frame, +X forward) and light state. */
export type BeamSource = Pick<VehicleState, 'pos' | 'rot' | 'lights'> & { id?: string; kind?: VehicleState['kind'] };

/** One car's pair of beams and their wall-occlusion ray fan. */
class BeamPair {
  readonly root = new T.Group();
  readonly lights: T.SpotLight[] = [];
  private nextQuery = 0;
  private querying = false;
  private generation = 0;
  private id = '';
  private depths = [Array(9).fill(145), Array(9).fill(145)];
  private q = new T.Quaternion();
  constructor(parent: T.Object3D, private physics: Pick<PhysicsClient, 'raycast'>, private queryMs: number) {
    parent.add(this.root);
    for (const sign of [-1, 1]) {
      const light = new T.SpotLight(0xffeed9, 0, 140, .38, .4, 2);
      light.castShadow = false; light.position.z = sign * .68;
      // Keep the light set stable through off/day/night; intensity controls cost without new pipelines.
      this.root.add(light, light.target); this.lights.push(light);
    }
  }
  update(state: BeamSource | null, now: number) {
    const mode = state?.lights ?? 0, key = `${state?.id}/${mode}`;
    if (key !== this.id) { this.id = key; this.depths = [Array(9).fill(145), Array(9).fill(145)]; this.nextQuery = 0; this.generation++; }
    const night = nightUniforms.night.value;
    this.lights.forEach(l => l.intensity = 0);
    if (!state) return;
    const truck = state.kind === 'truck', scooter = state.kind === 'scooter';
    this.root.position.copy(state.pos); this.root.quaternion.copy(state.rot);
    const range = mode === 2 ? 145 : 40;
    this.lights.forEach((light, i) => {
      // Truck: its lamp-front-left/right empties (trophy4400.glb, chassis frame).
      light.position.set(scooter ? .81 : truck ? 2.08 : 2.13, scooter ? .48 : truck ? .16 : -.05, (i ? 1 : -1) * (scooter ? .06 : truck ? .40 : .68));
      light.target.position.set(light.position.x + 30, light.position.y + (mode === 2 ? .3 : -1.2), light.position.z + (i ? 1.4 : -.35));
      light.angle = mode === 2 ? .48 : .42;
      light.decay = mode === 2 ? 1.1 : 1.8;
      light.distance = range;
      light.userData.mode = mode; light.userData.depths = this.depths[i];
      light.intensity = mode && night > .001 ? (mode === 2 ? 150 : 800) * night * (scooter ? .5 : 1) : 0;
    });
    if (!mode || night < .001 || this.querying || now < this.nextQuery) return;
    this.nextQuery = now + this.queryMs; this.querying = true;
    const generation = this.generation;
    this.q.copy(state.rot); this.root.updateWorldMatrix(true, true);
    // Conservative horizontal fan catches walls and parked vehicles, without treating the road as an occluder.
    const jobs = this.lights.map(light => {
      const origin = light.getWorldPosition(new T.Vector3());
      return Promise.all([-.42, -.315, -.21, -.105, 0, .105, .21, .315, .42].map(angle => {
        const dir = new T.Vector3(Math.cos(angle), 0, Math.sin(angle)).applyQuaternion(this.q);
        return this.physics.raycast(origin, dir, range, 'all').then(async hit => {
          // A long fleet body's chassis can contain the sedan's lamp mount. Keep wall occlusion
          // without allowing that vehicle's own box to extinguish its headlights.
          if (hit && hit.toi < .02) hit = await this.physics.raycast(origin, dir, range, 'solid');
          return hit ? hit.toi : range;
        });
      }));
    });
    void Promise.all(jobs).then(depths => { if (generation === this.generation) this.depths = depths; })
      .finally(() => this.querying = false);
  }
  /** Whether this pair is lighting car `id`. */
  holds(id: string | undefined) { return id !== undefined && this.id.startsWith(`${id}/`); }
  dispose() { this.generation++; }
}

export class VehicleBeams {
  readonly root = new T.Group();
  /** Every beam, the driven car's two first. */
  readonly lights: T.SpotLight[];
  private readonly own: BeamPair;
  private readonly remote: BeamPair[];
  private readonly picked: (BeamSource & { d: number })[] = [];
  constructor(scene: T.Object3D, physics: Pick<PhysicsClient, 'raycast'>) {
    this.root.name = 'vehicle-beams'; scene.add(this.root);
    this.own = new BeamPair(this.root, physics, 100);
    this.remote = Array.from({ length: REMOTE_BEAM_CARS }, () => new BeamPair(this.root, physics, 250));
    this.lights = [this.own, ...this.remote].flatMap(pair => pair.lights);
  }
  /** `remotes`: other players' cars as drawn (net/remotes.ts RemotePlayers.lamps); the nearest lit ones to `near`
   *  (the camera) within REMOTE_BEAM_M get the remaining pairs, a pair keeping its car while it stays chosen. */
  update(state: BeamSource | null, now: number, remotes: readonly BeamSource[] = [], near?: { x: number; y: number; z: number }) {
    this.own.update(state, now);
    const night = nightUniforms.night.value > .001, picked = this.picked;
    picked.length = 0;
    if (night && near) for (const r of remotes) {
      if (!r.lights) continue;
      const d = Math.hypot(r.pos.x - near.x, r.pos.y - near.y, r.pos.z - near.z);
      if (d < REMOTE_BEAM_M) picked.push({ ...r, d });
    }
    picked.sort((a, b) => a.d - b.d);
    picked.length = Math.min(picked.length, this.remote.length);
    // Same car, same pair: its occlusion fan is not thrown away when another car comes nearer.
    const slots: (BeamSource | null)[] = this.remote.map(() => null), rest = [...picked];
    this.remote.forEach((pair, i) => {
      const k = rest.findIndex(r => pair.holds(r.id));
      if (k >= 0) slots[i] = rest.splice(k, 1)[0];
    });
    for (let i = 0; i < slots.length && rest.length; i++) if (!slots[i]) slots[i] = rest.shift()!;
    this.remote.forEach((pair, i) => pair.update(slots[i], now));
  }
  dispose() { this.own.dispose(); for (const pair of this.remote) pair.dispose(); this.root.removeFromParent(); }
}
