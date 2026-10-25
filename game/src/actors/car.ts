// Car visual (procedural): chassis frame forward = +X, axle = Z, same dimensions as physics/world.ts CAR.
import * as THREE from 'three/webgpu';
import { CAR } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';

export class CarVisual {
  readonly root = new THREE.Group();
  private wheels: THREE.Group[] = [];

  constructor(scene: THREE.Scene) {
    this.root.name = 'car';
    const h = CAR.halfExt;
    const paint = new THREE.MeshStandardMaterial({ color: 0xc8202a, metalness: 0.5, roughness: 0.35 });
    const glass = new THREE.MeshStandardMaterial({ color: 0x223040, metalness: 0.2, roughness: 0.1 });
    const body = new THREE.Mesh(new THREE.BoxGeometry(h.x * 2, h.y * 2, h.z * 2), paint);
    const cabin = new THREE.Mesh(new THREE.BoxGeometry(h.x * 1.0, 0.5, h.z * 1.8), glass);
    cabin.position.set(-0.2, h.y + 0.25, 0);
    const light = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.12, 1.4), new THREE.MeshStandardMaterial({ color: 0xffffee, emissive: 0xffffcc, emissiveIntensity: 0.6 }));
    light.position.set(h.x, 0.05, 0);
    for (const m of [body, cabin, light]) { m.castShadow = true; m.receiveShadow = true; this.root.add(m); }
    const tyre = new THREE.MeshStandardMaterial({ color: 0x181818, roughness: 0.9 });
    const rim = new THREE.MeshStandardMaterial({ color: 0xaaaaaa, metalness: 0.8, roughness: 0.3 });
    const wg = new THREE.CylinderGeometry(CAR.wheelRadius, CAR.wheelRadius, 0.24, 20).rotateX(Math.PI / 2);
    const rg = new THREE.BoxGeometry(CAR.wheelRadius * 1.2, 0.08, 0.26);
    for (let i = 0; i < 4; i++) {
      const g = new THREE.Group();
      const spin = new THREE.Group();
      const t = new THREE.Mesh(wg, tyre); t.castShadow = true;
      spin.add(t, new THREE.Mesh(rg, rim));
      g.add(spin);
      this.root.add(g);
      this.wheels.push(g);
    }
    scene.add(this.root);
  }

  update(s: VehicleState) {
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    s.wheels.forEach((w, i) => {
      const g = this.wheels[i];
      g.position.set(w.pos.x, w.pos.y, w.pos.z);
      g.rotation.set(0, w.steer, 0);
      g.children[0].rotation.z = -w.rotation;
    });
  }
}
