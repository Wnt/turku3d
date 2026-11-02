import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CAR } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';

// The asset library has one player sedan. Concurrent consumers share preparation;
// Object3D clones keep independent suspension transforms and share immutable geometry.
const prototypes = new Map<string, Promise<THREE.Group>>();

async function prepareCar(url: string): Promise<THREE.Group> {
  const root = new THREE.Group();
  const wheels: THREE.Group[] = [];
  const { scene } = await new GLTFLoader().loadAsync(url);
  const body = scene.getObjectByName('body');
  if (!body) throw new Error('Car asset is missing body');
  body.removeFromParent();
  // Match the authored wheel arches to the physics wheelbase without stretching the tyres.
  body.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry = mesh.geometry.clone();
    const positions = mesh.geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i++) {
      const z = positions.getZ(i) - 0.025;
      const mapped = Math.abs(z) <= 0.66 ? z * (1.35 / 0.66)
        : Math.sign(z) * (1.35 + (Math.abs(z) - 0.66) * (0.75 / (z > 0 ? 0.59 : 0.64)));
      positions.setZ(i, mapped);
    }
    mesh.geometry.computeVertexNormals();
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
  });
  body.scale.set(1.2, 1.1, 1);
  body.rotation.y = Math.PI / 2;
  body.position.set(0, -0.55, 0);
  root.add(body);
  const names = ['wheel-front-left', 'wheel-front-right', 'wheel-back-left', 'wheel-back-right'];
  for (const name of names) {
    const wheel = scene.getObjectByName(name) as THREE.Mesh | undefined;
    if (!wheel?.isMesh) throw new Error(`Car asset is missing ${name}`);
    wheel.removeFromParent();
    const geometry = wheel.geometry.clone();
    geometry.computeBoundingBox();
    const bounds = geometry.boundingBox!;
    const center = bounds.getCenter(new THREE.Vector3());
    geometry.translate(-center.x, -center.y, -center.z);
    geometry.scale(0.24 / (bounds.max.x - bounds.min.x), CAR.wheelRadius / 0.3, CAR.wheelRadius / 0.3);
    geometry.rotateY(Math.PI / 2);
    wheel.geometry = geometry;
    wheel.position.set(0, 0, 0);
    wheel.rotation.set(0, 0, 0);
    const steering = new THREE.Group(); steering.name = `vehicle-wheel-${wheels.length}`; steering.add(wheel);
    root.add(steering); wheels.push(steering);
  }
  root.traverse(o => { if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return root;
}

/** Kenney CC0 sedan, +X forward. Authored wheels are independently steered and suspended. */
export class CarVisual {
  readonly root = new THREE.Group();
  private wheels: THREE.Group[] = [];
  loaded = false;

  constructor(scene: THREE.Object3D) { this.root.name = 'car'; scene.add(this.root); }

  async load(url: string) {
    let pending = prototypes.get(url);
    if (!pending) {
      pending = prepareCar(url).catch(error => { prototypes.delete(url); throw error; });
      prototypes.set(url, pending);
    }
    const prototype = await pending;
    const model = prototype.clone(true);
    this.wheels = model.children.filter(child => child.name.startsWith('vehicle-wheel-')) as THREE.Group[];
    this.root.clear(); this.root.add(model);
    this.loaded = true;
  }

  update(s: VehicleState) {
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    s.wheels.forEach((w, i) => {
      const g = this.wheels[i];
      if (!g) return;
      g.position.set(w.pos.x, w.pos.y, w.pos.z);
      g.rotation.set(0, w.steer, 0);
      g.children[0].rotation.z = -w.rotation;
    });
  }
}

export { ParkedCarVisuals, type ParkedRest, type TrafficInstance } from './fleet/visuals';
