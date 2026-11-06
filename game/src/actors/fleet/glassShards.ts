import * as THREE from 'three/webgpu';
import { onDamage, type ShatterEvent } from './damageRig';

// Glass shards of shattered car windows (vehicle/README.md "Glass"): when a drawn car's damage state shows a window
// newly shattered (damageRig.ts), a burst of small glass flakes leaves the window, falls to the ground under the car
// and lies there a while. One instanced draw for every burst; plain ballistic motion on the main thread (the ground is
// the car's own wheel plane), nothing in physics and nothing synced: every client throws its own from the shared state.

const SHARDS = 36, CAPACITY = 8 * SHARDS, LIE_S = 25, FADE_S = 3;

export class GlassShards {
  private readonly mesh: THREE.InstancedMesh;
  private readonly shards = Array.from({ length: CAPACITY }, () => ({ alive: false, age: 0, ground: 0, size: 1,
    p: new THREE.Vector3(), v: new THREE.Vector3(), q: new THREE.Quaternion(), w: new THREE.Vector3(), resting: false }));
  private next = 0;
  private active = 0;
  private readonly matrix = new THREE.Matrix4();
  private readonly spin = new THREE.Quaternion();
  constructor(scene: THREE.Object3D) {
    // A thin irregular flake, a few centimetres across.
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, .035, .006, .01, .012, 0, .04, -.02, .004, .018], 3));
    geometry.setIndex([0, 1, 2, 0, 2, 3]);
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardNodeMaterial({ name: 'glass shards', color: 0xe4eef0, roughness: .05, metalness: .2,
      emissive: 0x1a2224, transparent: true, opacity: .85, side: THREE.DoubleSide, depthWrite: false });
    this.mesh = new THREE.InstancedMesh(geometry, material, CAPACITY);
    this.mesh.name = 'glass-shards'; this.mesh.count = 0; this.mesh.frustumCulled = false;
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    scene.add(this.mesh);
    this.unsubscribe = onDamage('shatter', event => this.burst(event));
  }
  private burst(event: ShatterEvent) {
    event.glass.updateWorldMatrix(true, false);
    const world = event.glass.matrixWorld, origin = event.centre.clone().applyMatrix4(world);
    // The car's wheel plane (the fleet frame's y = 0) is the ground the shards land on; they fly out of the window.
    const ground = new THREE.Vector3(event.centre.x, 0, event.centre.z).applyMatrix4(world).y;
    const out = new THREE.Vector3(event.centre.x, 0, event.centre.z).transformDirection(world).setY(0).normalize();
    for (let k = 0; k < SHARDS; k++) {
      const s = this.shards[this.next];
      this.next = (this.next + 1) % CAPACITY;
      if (!s.alive) this.active++;
      s.alive = true; s.age = 0; s.resting = false; s.ground = ground + .004; s.size = .6 + Math.random() * .9;
      s.p.copy(origin).add(new THREE.Vector3((Math.random() - .5) * .5, (Math.random() - .5) * .3, (Math.random() - .5) * .5));
      s.v.copy(event.velocity).addScaledVector(out, .8 + Math.random() * 1.8)
        .add(new THREE.Vector3((Math.random() - .5) * 1.6, .4 + Math.random() * 1.4, (Math.random() - .5) * 1.6));
      s.q.setFromEuler(new THREE.Euler(Math.random() * 6.3, Math.random() * 6.3, Math.random() * 6.3));
      s.w.set((Math.random() - .5) * 18, (Math.random() - .5) * 18, (Math.random() - .5) * 18);
    }
  }
  /** Steps the flying shards and lays the landed ones flat; nothing runs while none are alive. */
  update(dt: number) {
    if (!this.active || dt <= 0) return;
    dt = Math.min(dt, .05);
    let count = 0;
    for (const s of this.shards) {
      if (!s.alive) continue;
      s.age += dt;
      if (s.age > LIE_S + FADE_S) { s.alive = false; this.active--; continue; }
      if (!s.resting) {
        s.v.y -= 9.81 * dt;
        s.p.addScaledVector(s.v, dt);
        this.spin.setFromEuler(new THREE.Euler(s.w.x * dt, s.w.y * dt, s.w.z * dt));
        s.q.multiply(this.spin);
        if (s.p.y <= s.ground) {
          s.p.y = s.ground;
          // A bounce or two, then flat on the ground.
          if (Math.abs(s.v.y) < 1.2) { s.resting = true; s.q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.random() * 6.3); }
          else { s.v.y *= -.25; s.v.x *= .5; s.v.z *= .5; s.w.multiplyScalar(.5); }
        }
      }
      const fade = s.age > LIE_S ? 1 - (s.age - LIE_S) / FADE_S : 1;
      this.matrix.compose(s.p, s.q, new THREE.Vector3(1, 1, 1).multiplyScalar(s.size * fade));
      this.mesh.setMatrixAt(count++, this.matrix);
    }
    this.mesh.count = count;
    this.mesh.instanceMatrix.needsUpdate = true;
  }
  /** Shards alive now (flying and lying), for the developer console and probes. */
  get alive() { return this.active; }
  private readonly unsubscribe: () => void;
  dispose() { this.unsubscribe(); this.mesh.removeFromParent(); this.mesh.geometry.dispose(); (this.mesh.material as THREE.Material).dispose(); }
}
