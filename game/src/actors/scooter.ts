import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { SCOOTER } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import { scooterLean } from '../vehicle/lean';

/** MiniPoly CC BY 3.0 scooter (public/assets/vehicles/README.md), baked to the physics frame: +X forward,
 *  tyre contact at y = 0. One mesh, so the wheels do not spin; the body leans with the turn. */
export class ScooterVisual {
  readonly root = new THREE.Group();
  /** Rider hip anchor, facing +X like the chassis (the character model faces +Z). */
  readonly seat = new THREE.Object3D();
  private readonly lean = new THREE.Group();
  private angle = 0;
  loaded = false;

  constructor(scene: THREE.Scene) {
    this.root.name = 'scooter';
    this.root.visible = false;
    this.root.add(this.lean);
    this.seat.position.set(-0.42, 0.84, 0);
    this.seat.rotation.y = Math.PI / 2;
    this.lean.add(this.seat);
    scene.add(this.root);
  }

  async load(url: string) {
    const { scene } = await new GLTFLoader().loadAsync(url);
    this.lean.add(scene);
    // Courier top box on the rear rack: a red insulated pizza case with a lighter lid.
    const box = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.34, 0.42), new THREE.MeshStandardMaterial({ color: 0xc8553d, roughness: 0.8 }));
    box.position.set(-0.86, 0.9, 0);
    const lid = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.04, 0.44), new THREE.MeshStandardMaterial({ color: 0xefe6d8, roughness: 0.8 }));
    lid.position.set(-0.86, 1.09, 0);
    box.name = 'pizza-box';
    this.lean.add(box, lid);
    this.root.traverse(o => { if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    this.loaded = true;
  }

  update(dt: number, s: VehicleState) {
    this.root.visible = this.loaded;
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    const [front, rear] = s.wheels;
    if (front && rear) this.lean.position.y = (front.pos.y + rear.pos.y) / 2 - SCOOTER.wheelRadius;
    // Positive steer turns left; the body leans into the turn (negative roll about +X).
    const target = -scooterLean(s.forwardSpeed, front?.steer ?? 0);
    this.angle += (target - this.angle) * (1 - Math.exp(-dt * 6));
    this.lean.rotation.x = this.angle;
  }

  /** Cosmetic lean (radians, positive to the right) for checks. */
  get leanAngle() { return this.angle; }
}
