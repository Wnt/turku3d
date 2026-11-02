import { textureUploads } from '../render/textureUploads';
// CC0 MakeHuman player, retargeted to the Quaternius UAL rig and locomotion clips.
import * as THREE from 'three/webgpu';
import { FootIK, type FootRaycast } from './footIK';
import { MAT } from '../shared/col/materials';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { sharedKtx } from '../world/loadingKtx';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { LOCOMOTION as LOCO, locomotionFrequency, locomotionStrideScale, locomotionWeights, locomotionBodyHeight } from './locomotion';

export const SPEED = { walk: 1.5, run: 3.6, sprint: 6.0 } as const;

export class PlayerVisual {
  readonly root = new THREE.Group();
  private mixer: THREE.AnimationMixer | null = null;
  private loco: THREE.AnimationAction[] = [];
  private air: THREE.AnimationAction | null = null;
  private airWeight = 0;
  private stair: THREE.AnimationAction | null = null;
  private seated: THREE.AnimationAction | null = null;
  private hips: THREE.Object3D | null = null;
  private stairWeight = 0;
  private heading = 0;
  loaded = false;
  locomotion = 'idle';
  private ik: FootIK | null = null;
  private positioned = false;
  private phase = 0;
  get contacts() { return this.ik?.contacts ?? []; }

  constructor(scene: THREE.Object3D, private raycast: FootRaycast) {
    this.root.name = 'player';
    scene.add(this.root);
  }

  async load(url: string, renderer: THREE.WebGPURenderer) {
    const ktx2 = sharedKtx(renderer);
    const loader = new GLTFLoader().register(() => textureUploads(renderer).plugin()).setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.loadAsync(url);
    const model = gltf.scene;
    model.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; m.frustumCulled = false; } });
    this.root.add(model);
    this.ik = new FootIK(this.root, model, this.raycast);
    this.mixer = new THREE.AnimationMixer(model);
    const find = (n: string) => gltf.animations.find((a) => a.name === n);
    for (const l of LOCO) {
      const clip = find(l.clip);
      if (!clip) throw new Error(`character clip ${l.clip} missing`);
      const a = this.mixer.clipAction(clip);
      a.play(); a.setEffectiveWeight(l.speed === 0 ? 1 : 0);
      this.loco.push(a);
    }
    const stairClip = find('Walk_Formal_Loop');
    if (stairClip) { this.stair = this.mixer.clipAction(stairClip).play(); this.stair.setEffectiveWeight(0); }
    const seated = find('Driving_Loop');
    if (!seated) throw new Error('character clip Driving_Loop missing');
    this.seated = this.mixer.clipAction(seated).play();
    this.seated.setEffectiveWeight(0);
    this.hips = model.getObjectByName('DEF-hips') ?? null;
    const jl = find('Jump_Loop');
    if (jl) { this.air = this.mixer.clipAction(jl); this.air.play(); this.air.setEffectiveWeight(0); }
    this.loaded = true;
  }

  /** feet: physics feet position; vel: actual velocity; grounded from the KCC. */
  update(dt: number, feet: THREE.Vector3, vel: THREE.Vector3, grounded: boolean, material: number) {
    const oldY = this.root.position.y;
    const smooth = this.positioned && grounded && this.root.position.distanceTo(feet) < 1;
    this.root.position.copy(feet);
    if (smooth) this.root.position.y = oldY + (feet.y - oldY) * (1 - Math.exp(-dt * 20));
    this.positioned = true;
    this.ik?.restore();
    const hs = Math.hypot(vel.x, vel.z);
    if (hs > 0.3) {
      const target = Math.atan2(vel.x, vel.z); // model faces +Z
      let d = target - this.heading; d = Math.atan2(Math.sin(d), Math.cos(d));
      this.heading += d * Math.min(1, dt * 12);
    }
    this.root.rotation.y = this.heading;
    this.root.rotation.x = this.root.rotation.z = 0;
    this.seated?.setEffectiveWeight(0);
    const stairs = grounded && material === MAT.stair_slope;
    this.locomotion = !grounded ? 'air' : stairs ? (vel.y > 0.05 ? 'stairUp' : vel.y < -0.05 ? 'stairDown' : 'stairIdle')
      : hs < 0.1 ? 'idle' : hs < 1.8 ? 'walk' : hs < 4.3 ? 'jog' : 'sprint';
    if (!this.mixer) return;
    const w = locomotionWeights(hs);
    this.airWeight += ((grounded ? 0 : 1) - this.airWeight) * Math.min(1, dt * 8);
    this.stairWeight += ((stairs && hs > 0.1 ? 1 : 0) - this.stairWeight) * (1 - Math.exp(-dt * 12));
    this.phase = (this.phase + dt * locomotionFrequency(hs, w, this.stairWeight)) % 1;
    this.loco.forEach((a, k) => {
      a.setEffectiveWeight(w[k] * (1 - this.airWeight) * (1 - this.stairWeight));
      a.timeScale = k === 0 ? 1 : 0;
      if (k > 0) a.time = this.phase * a.getClip().duration;
    });
    if (this.stair) {
      this.stair.setEffectiveWeight(this.stairWeight * (1 - this.airWeight));
      this.stair.timeScale = 0;
      this.stair.time = this.phase * this.stair.getClip().duration;
    }
    this.air?.setEffectiveWeight(this.airWeight);
    this.mixer.update(dt);
    const hipHeight = this.hips ? this.hips.getWorldPosition(new THREE.Vector3()).y - this.root.position.y : undefined;
    this.ik?.update(dt, grounded, stairs, locomotionStrideScale(w, this.stairWeight),
      hipHeight === undefined ? undefined : locomotionBodyHeight(hipHeight, w, this.stairWeight));
  }

  /** Seated on a two-wheeler: the UAL driving pose, hips placed on `seat` (its +Z faces forward). */
  ride(dt: number, seat: THREE.Object3D) {
    this.locomotion = 'ride';
    this.positioned = false;
    this.ik?.restore();
    for (const action of this.loco) action.setEffectiveWeight(0);
    this.stair?.setEffectiveWeight(0);
    this.air?.setEffectiveWeight(0);
    this.airWeight = this.stairWeight = 0;
    this.seated?.setEffectiveWeight(1);
    this.mixer?.update(dt);
    seat.updateWorldMatrix(true, false);
    seat.matrixWorld.decompose(this.root.position, this.root.quaternion, this.root.scale);
    this.heading = Math.atan2(2 * (this.root.quaternion.w * this.root.quaternion.y + this.root.quaternion.x * this.root.quaternion.z),
      1 - 2 * (this.root.quaternion.y ** 2 + this.root.quaternion.x ** 2));
    if (!this.hips) return;
    // Shift the root so the posed hips sit on the seat anchor.
    this.root.updateMatrixWorld(true);
    const hips = this.hips.getWorldPosition(new THREE.Vector3());
    const anchor = new THREE.Vector3().setFromMatrixPosition(seat.matrixWorld);
    this.root.position.add(anchor.sub(hips));
  }
}
