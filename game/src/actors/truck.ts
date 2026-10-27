import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { TRUCK } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';

/** Body-local Kenney frame of `truck.glb` (public/assets/vehicles/README.md): +Z forward, wheel centres at
 *  z 0.885 / -0.735, a 1.5 m wide body whose bottom sits 0.15 m above the tyre contact. */
const KENNEY = { axle: 0.075, halfBase: 0.81, groundBelowBody: 0.15, wheelRadius: 0.3 };
/** Uniform scale of the pickup's cross-section and overhangs; the cab and bed stretch to the physics wheelbase. */
const SCALE = 1.35;
/** The scaled pickup's own tyre line sits this far below the chassis centre: 25 cm of lift above the physics ground
 *  at rest (0.95 m below the centre), so the bigger tyres fill the arches. */
const BODY_GROUND = -0.7;
const TYRE_WIDTH = 0.42;
const CAGE = new THREE.MeshStandardMaterial({ color: 0x2b2f36, roughness: 0.55 });
const SHOCK = new THREE.MeshStandardMaterial({ color: 0xd8b13a, roughness: 0.45 });
const UP = new THREE.Vector3(0, 1, 0), hub = new THREE.Vector3(), span = new THREE.Vector3();
const LENS = new THREE.MeshStandardMaterial({ color: 0xfff4d6, emissive: 0xfff1c4, emissiveIntensity: 0.6, roughness: 0.3 });

/** Kenney body-local point to the chassis frame (+X forward, +Y up, -Z left). */
export function truckBodyPoint(x: number, y: number, z: number): THREE.Vector3 {
  const d = z - KENNEY.axle, half = TRUCK.wheels[0].x;
  const forward = Math.abs(d) <= KENNEY.halfBase ? d * half / KENNEY.halfBase
    : Math.sign(d) * (half + (Math.abs(d) - KENNEY.halfBase) * SCALE);
  return new THREE.Vector3(forward, BODY_GROUND + (y + KENNEY.groundBelowBody) * SCALE, -x * SCALE);
}

function tube(a: THREE.Vector3, b: THREE.Vector3, radius = 0.035, material = CAGE) {
  const length = a.distanceTo(b);
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 8), material);
  mesh.position.copy(a).add(b).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(UP, b.clone().sub(a).normalize());
  return mesh;
}

/** Trophy truck built from Kenney's CC0 Car Kit pickup: the body lifted and stretched to the physics wheelbase,
 *  four 0.92 m tyres from the kit's wheel, and in code a roll cage over the bed, a roof light bar, a spare tyre and
 *  yellow coilovers that follow the suspension. The driver stays visible through the open window frames. */
export class TruckVisual {
  readonly root = new THREE.Group();
  /** Driver hip anchor, facing +X like the chassis (the character model faces +Z). */
  readonly seat = new THREE.Object3D();
  private wheels: THREE.Group[] = [];
  private shocks: { mesh: THREE.Mesh; top: THREE.Vector3 }[] = [];
  loaded = false;

  constructor(scene: THREE.Object3D) {
    this.root.name = 'truck';
    this.root.visible = false;
    this.seat.position.set(0.05, 0.02, -0.42);
    this.seat.rotation.y = Math.PI / 2;
    this.root.add(this.seat);
    scene.add(this.root);
  }

  async load(url: string) {
    const { scene } = await new GLTFLoader().loadAsync(url);
    const body = scene.getObjectByName('body') as THREE.Mesh | undefined;
    const wheel = scene.getObjectByName('wheel') as THREE.Mesh | undefined;
    if (!body?.isMesh || !wheel?.isMesh) throw new Error('Truck asset is missing body or wheel');
    const geometry = body.geometry.clone();
    const positions = geometry.getAttribute('position');
    for (let i = 0; i < positions.count; i++) {
      const p = truckBodyPoint(positions.getX(i), positions.getY(i), positions.getZ(i));
      positions.setXYZ(i, p.x, p.y, p.z);
    }
    // A quarter turn and positive scales keep the winding; only the normals follow the stretch.
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    this.root.add(new THREE.Mesh(geometry, body.material));

    const tyre = wheel.geometry.clone();
    tyre.computeBoundingBox();
    const bounds = tyre.boundingBox!, centre = bounds.getCenter(new THREE.Vector3());
    tyre.translate(-centre.x, -centre.y, -centre.z);
    const radius = TRUCK.wheelRadius / KENNEY.wheelRadius;
    tyre.scale(TYRE_WIDTH / (bounds.max.x - bounds.min.x), radius, radius);
    // Kenney's left wheel faces its hub to +X (left); turned, the hub faces -Z, the chassis frame's left.
    tyre.rotateY(Math.PI / 2);
    const mirrored = tyre.clone().scale(1, 1, -1);
    const index = mirrored.getIndex()!;
    for (let i = 0; i < index.count; i += 3) { const a = index.getX(i + 1); index.setX(i + 1, index.getX(i + 2)); index.setX(i + 2, a); }
    mirrored.computeVertexNormals();
    for (const at of TRUCK.wheels) {
      const steering = new THREE.Group();
      steering.add(new THREE.Mesh(at.z < 0 ? tyre : mirrored, wheel.material));
      this.root.add(steering);
      this.wheels.push(steering);
      // Coilover from a mount inboard of the wheel, above the frame, down to the hub.
      const top = new THREE.Vector3(at.x - Math.sign(at.x) * 0.2, 0.35, at.z * 0.55);
      const mesh = tube(top, new THREE.Vector3(at.x, 0, at.z * 0.85), 0.06, SHOCK);
      this.shocks.push({ mesh, top });
      this.root.add(mesh);
    }
    const spare = new THREE.Mesh(tyre, wheel.material);
    spare.rotation.x = Math.PI / 2;
    spare.position.set(-1.45, 0.08, 0);
    this.root.add(spare, ...this.cage());
    this.root.traverse(o => { if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    // Wheels at the static ride height until the first physics state, so a clone (a remote player's truck) stands.
    const sag = 9.81 / (4 * TRUCK.suspensionStiffness);
    this.placeWheels(TRUCK.wheels.map(at => ({ pos: { x: at.x, y: at.y - TRUCK.rest + sag, z: at.z }, steer: 0, rotation: 0 })));
    this.loaded = true;
  }

  /** Hoop behind the cab, tubes down to the tailgate, a mid hoop over the spare, a roof light bar and a front bar. */
  private cage(): THREE.Object3D[] {
    const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
    const parts: THREE.Object3D[] = [];
    const rail = 0.38, top = 1.12, cab = -0.5, tail = -2.3, mid = -1.45;
    const midTop = top + (rail + 0.07 - top) * (cab - mid) / (cab - tail);
    for (const side of [-1, 1]) {
      parts.push(tube(v(cab, rail, 0.86 * side), v(cab, top, 0.78 * side)));
      parts.push(tube(v(cab, top, 0.78 * side), v(tail, rail + 0.07, 0.86 * side)));
      parts.push(tube(v(mid, rail, 0.86 * side), v(mid, midTop, 0.82 * side)));
      // Front bar posts from the bumper up to the bar.
      parts.push(tube(v(2.2, -0.42, 0.55 * side), v(2.42, -0.12, 0.6 * side), 0.04));
    }
    parts.push(tube(v(cab, top, -0.78), v(cab, top, 0.78)));
    parts.push(tube(v(mid, midTop, -0.82), v(mid, midTop, 0.82)));
    parts.push(tube(v(cab, top, -0.78), v(mid, midTop, 0.82)));
    parts.push(tube(v(2.42, -0.12, -0.75), v(2.42, -0.12, 0.75), 0.045));
    const bar = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.12, 1.5), CAGE);
    bar.position.set(0.5, 1.13, 0);
    parts.push(bar);
    for (const z of [-0.54, -0.18, 0.18, 0.54]) {
      const lens = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.08, 0.3), LENS);
      lens.position.set(0.58, 1.13, z);
      parts.push(lens);
    }
    return parts;
  }

  update(s: VehicleState) {
    this.root.visible = this.loaded;
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    this.placeWheels(s.wheels);
  }

  private placeWheels(wheels: readonly Pick<VehicleState['wheels'][number], 'pos' | 'steer' | 'rotation'>[]) {
    wheels.forEach((w, i) => {
      const g = this.wheels[i], shock = this.shocks[i];
      if (!g || !shock) return;
      g.position.set(w.pos.x, w.pos.y, w.pos.z);
      g.rotation.set(0, w.steer, 0);
      g.children[0].rotation.z = -w.rotation;
      // The coilover spans its frame mount and the hub, so its length shows the suspension travel.
      span.subVectors(hub.set(w.pos.x, w.pos.y, w.pos.z * 0.85), shock.top);
      const length = span.length();
      shock.mesh.position.copy(shock.top).addScaledVector(span, 0.5);
      shock.mesh.quaternion.setFromUnitVectors(UP, span.divideScalar(length));
      shock.mesh.scale.y = length / (shock.mesh.geometry as THREE.CylinderGeometry).parameters.height;
    });
  }
}
