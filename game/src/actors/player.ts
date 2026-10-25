// Player visual: Quaternius UAL mannequin (CC0) with idle / walk / jog / sprint blended by ground speed.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

/** Native forward speed (m/s) at timeScale 1 of each locomotion clip (tuned by eye; foot sliding tolerance). */
const LOCO = [
  { clip: 'Idle_Loop', speed: 0 },
  { clip: 'Walk_Loop', speed: 1.5 },
  { clip: 'Jog_Fwd_Loop', speed: 3.6 },
  { clip: 'Sprint_Loop', speed: 6.0 },
] as const;

export const SPEED = { walk: 1.5, run: 3.6, sprint: 6.0 } as const;

export class PlayerVisual {
  readonly root = new THREE.Group();
  private mixer: THREE.AnimationMixer | null = null;
  private loco: THREE.AnimationAction[] = [];
  private air: THREE.AnimationAction | null = null;
  private airWeight = 0;
  private heading = 0;
  loaded = false;

  constructor(scene: THREE.Scene) {
    this.root.name = 'player';
    scene.add(this.root);
  }

  async load(url: string) {
    const gltf = await new GLTFLoader().loadAsync(url);
    const model = gltf.scene;
    model.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; m.frustumCulled = false; } });
    this.root.add(model);
    this.mixer = new THREE.AnimationMixer(model);
    const find = (n: string) => gltf.animations.find((a) => a.name === n);
    for (const l of LOCO) {
      const clip = find(l.clip);
      if (!clip) throw new Error(`character clip ${l.clip} missing`);
      const a = this.mixer.clipAction(clip);
      a.play(); a.setEffectiveWeight(l.speed === 0 ? 1 : 0);
      this.loco.push(a);
    }
    const jl = find('Jump_Loop');
    if (jl) { this.air = this.mixer.clipAction(jl); this.air.play(); this.air.setEffectiveWeight(0); }
    this.loaded = true;
  }

  /** feet: physics feet position; vel: actual velocity; grounded from the KCC. */
  update(dt: number, feet: THREE.Vector3, vel: THREE.Vector3, grounded: boolean) {
    this.root.position.copy(feet);
    const hs = Math.hypot(vel.x, vel.z);
    if (hs > 0.3) {
      const target = Math.atan2(vel.x, vel.z); // model faces +Z
      let d = target - this.heading; d = Math.atan2(Math.sin(d), Math.cos(d));
      this.heading += d * Math.min(1, dt * 12);
    }
    this.root.rotation.y = this.heading;
    if (!this.mixer) return;
    // piecewise-linear blend between neighbouring locomotion clips
    const w = new Array(LOCO.length).fill(0);
    if (hs <= LOCO[0].speed) w[0] = 1;
    else if (hs >= LOCO[LOCO.length - 1].speed) w[LOCO.length - 1] = 1;
    else for (let k = 0; k < LOCO.length - 1; k++) {
      if (hs >= LOCO[k].speed && hs <= LOCO[k + 1].speed) { const t = (hs - LOCO[k].speed) / (LOCO[k + 1].speed - LOCO[k].speed); w[k] = 1 - t; w[k + 1] = t; }
    }
    this.airWeight += ((grounded ? 0 : 1) - this.airWeight) * Math.min(1, dt * 8);
    this.loco.forEach((a, k) => {
      a.setEffectiveWeight(w[k] * (1 - this.airWeight));
      a.timeScale = LOCO[k].speed > 0 ? THREE.MathUtils.clamp(hs / LOCO[k].speed, 0.5, 1.6) : 1;
    });
    this.air?.setEffectiveWeight(this.airWeight);
    this.mixer.update(dt);
  }
}
