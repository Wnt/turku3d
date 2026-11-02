import * as T from 'three/webgpu';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import { nightUniforms } from '../render/nightLights';

/** Exactly two shadowless spotlights for the driven car. Regular PBR lighting also reads existing wet-road BRDFs. */
export class VehicleBeams {
  readonly root = new T.Group();
  readonly lights: T.SpotLight[] = [];
  private nextQuery = 0;
  private querying = false;
  private generation = 0;
  private id = '';
  private depths = [Array(9).fill(145), Array(9).fill(145)];
  private q = new T.Quaternion();
  constructor(scene: T.Object3D, private physics: Pick<PhysicsClient, 'raycast'>) {
    this.root.name = 'vehicle-beams'; scene.add(this.root);
    for (const sign of [-1, 1]) {
      const light = new T.SpotLight(0xffeed9, 0, 140, .38, .4, 2);
      light.castShadow = false; light.position.z = sign * .68;
      // Keep the light set stable through off/day/night; intensity controls cost without new pipelines.
      this.root.add(light, light.target); this.lights.push(light);
    }
  }
  update(state: VehicleState | null, now: number) {
    const mode = state?.lights ?? 0, key = `${state?.id}/${mode}`;
    if (key !== this.id) { this.id = key; this.depths = [Array(9).fill(145), Array(9).fill(145)]; this.nextQuery = 0; this.generation++; }
    const night = nightUniforms.night.value;
    this.lights.forEach(l => l.intensity = 0);
    if (!state) return;
    const truck = state.kind === 'truck', scooter = state.kind === 'scooter';
    this.root.position.copy(state.pos); this.root.quaternion.copy(state.rot);
    const range = mode === 2 ? 145 : 40;
    this.lights.forEach((light, i) => {
      light.position.set(scooter ? .81 : truck ? 2.40 : 2.13, scooter ? .48 : truck ? .15 : -.05, (i ? 1 : -1) * (scooter ? .06 : .68));
      light.target.position.set(light.position.x + 30, light.position.y + (mode === 2 ? .3 : -1.2), light.position.z + (i ? 1.4 : -.35));
      light.angle = mode === 2 ? .48 : .42;
      light.decay = mode === 2 ? 1.1 : 1.8;
      light.distance = range;
      light.userData.mode = mode; light.userData.depths = this.depths[i];
      light.intensity = mode && night > .001 ? 200 * night * (scooter ? .5 : 1) : 0;
    });
    if (!mode || night < .001 || this.querying || now < this.nextQuery) return;
    this.nextQuery = now + 100; this.querying = true;
    const generation = this.generation;
    this.q.copy(state.rot); this.root.updateWorldMatrix(true, true);
    // Conservative horizontal fan catches walls and parked vehicles, without treating the road as an occluder.
    const jobs = this.lights.map(light => {
      const origin = light.getWorldPosition(new T.Vector3());
      return Promise.all([-.42, -.315, -.21, -.105, 0, .105, .21, .315, .42].map(angle => {
        const dir = new T.Vector3(Math.cos(angle), 0, Math.sin(angle)).applyQuaternion(this.q);
        return this.physics.raycast(origin, dir, range, 'all').then(hit => hit ? hit.toi : range);
      }));
    });
    void Promise.all(jobs).then(depths => { if (generation === this.generation) this.depths = depths; })
      .finally(() => this.querying = false);
  }
  dispose() { this.generation++; this.root.removeFromParent(); }
}
