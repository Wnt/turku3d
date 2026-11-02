import * as T from 'three/webgpu';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import { nightUniforms } from '../render/nightLights';

/** Soft asymmetric dipped cutoff or a full high-beam ellipse; shared by both headlights. */
function cookie(dipped: boolean) {
  const size = 64, bytes = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = (x + .5) / size * 2 - 1, v = (y + .5) / size * 2 - 1;
    const edge = Math.max(0, Math.min(1, (1 - Math.hypot(u, v)) * 5));
    // Right-hand traffic: lower cutoff on the oncoming (left) side, raised verge on the right.
    const cutoff = dipped ? Math.max(0, Math.min(1, (v + .08 + Math.max(0, u) * .18) * 24)) : 1;
    const value = Math.round(255 * edge * cutoff);
    const i = (y * size + x) * 4; bytes[i] = bytes[i + 1] = bytes[i + 2] = value; bytes[i + 3] = 255;
  }
  const texture = new T.DataTexture(bytes, size, size); texture.needsUpdate = true;
  texture.minFilter = texture.magFilter = T.LinearFilter;
  return texture;
}

/** Exactly two shadowless spotlights for the driven car. Regular PBR lighting also reads existing wet-road BRDFs. */
export class VehicleBeams {
  readonly root = new T.Group();
  readonly lights: T.SpotLight[] = [];
  private dipped = cookie(true);
  private high = cookie(false);
  private nextQuery = 0;
  private querying = false;
  private generation = 0;
  private id = '';
  private limits = [140, 140];
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
    if (key !== this.id) { this.id = key; this.limits = [140, 140]; this.nextQuery = 0; this.generation++; }
    const night = nightUniforms.night.value;
    this.lights.forEach(l => l.intensity = 0);
    if (!state) return;
    const truck = state.kind === 'truck', scooter = state.kind === 'scooter';
    this.root.position.copy(state.pos); this.root.quaternion.copy(state.rot);
    const range = mode === 2 ? 145 : 40;
    this.lights.forEach((light, i) => {
      light.position.set(scooter ? .81 : truck ? 2.40 : 2.13, scooter ? .48 : truck ? .15 : -.05, (i ? 1 : -1) * (scooter ? .06 : .68));
      light.target.position.set(light.position.x + 30, light.position.y + (mode === 2 ? .3 : -1.2), light.position.z + (i ? 1.4 : -.35));
      light.angle = mode === 2 ? .38 : .42;
      light.map = mode === 2 ? this.high : this.dipped;
      light.distance = Math.min(range, this.limits[i]);
      light.intensity = mode && night > .001 ? (mode === 2 ? 42000 : 8500) * night * (scooter ? .5 : 1) : 0;
    });
    if (!mode || night < .001 || this.querying || now < this.nextQuery) return;
    this.nextQuery = now + 100; this.querying = true;
    const generation = this.generation;
    this.q.copy(state.rot); this.root.updateWorldMatrix(true, true);
    // Conservative horizontal fan catches walls and parked vehicles, without treating the road as an occluder.
    const jobs = this.lights.map(light => {
      const origin = light.getWorldPosition(new T.Vector3());
      return Promise.all([-.32, -.16, 0, .16, .32].map(angle => {
        const dir = new T.Vector3(Math.cos(angle), 0, Math.sin(angle)).applyQuaternion(this.q);
        return this.physics.raycast(origin, dir, range, 'all').then(hit => hit ? hit.toi + .15 : range);
      })).then(distances => Math.min(...distances));
    });
    void Promise.all(jobs).then(limits => { if (generation === this.generation) this.limits = limits; })
      .finally(() => this.querying = false);
  }
  dispose() { this.generation++; this.root.removeFromParent(); this.dipped.dispose(); this.high.dispose(); }
}
