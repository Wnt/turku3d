import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CAR } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import type { Manifest, ParkedVehicle } from '../world/manifest';
import type { ParkedPose } from '../world/parkedVehicles';
import { paintActor, paintBatch, paintInstances, paintMaterial, recolourable } from './parkedPaint';
import { CHASSIS_LIFT, TRAFFIC_MODELS } from '../traffic/constants';
import type { Quat } from '../physics/protocol';

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

/** Pose source for draft cars that have no physics body (world/parkedVehicles.ts). */
export interface ParkedRest { restingPose(car: ParkedVehicle): ParkedPose | null }

interface Batch { mesh: THREE.InstancedMesh; cars: ParkedVehicle[]; trafficPaints: string }
/** A moving ambient car drawn after a batch's parked instances (traffic/client.ts): chassis centre and rotation. */
export interface TrafficInstance { model: string; color: string; x: number; y: number; z: number; q: Quat }
const LOCAL_AXIS = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);

/** Parked cars share one draw per body model (one per GLB for models without a known paint swatch)
 * until one is driven; driven and remote cars keep their own scene-level mesh with the same material. */
export class ParkedCarVisuals {
  private manifest: Manifest | null = null;
  private groups = new Map<string, Batch>();
  private sources = new Map<string, THREE.Mesh>();
  private loading = new Map<string, Promise<THREE.Mesh>>();
  private dynamic = new Map<string, THREE.Mesh>();
  private matrix = new THREE.Matrix4();
  private position = new THREE.Vector3();
  private rotation = new THREE.Quaternion();
  private scale = new THREE.Vector3(1, 1, 1);
  /** Instances each traffic body model keeps free after its parked cars (ambient traffic shares the draw). */
  private trafficReserve = 0;
  private traffic = new Map<string, TrafficInstance[]>();

  constructor(private readonly scene: THREE.Object3D,
    private readonly prepare: (model: THREE.Object3D) => Promise<void> = async () => {}) {}

  private source(batch: string, key: string): Promise<THREE.Mesh> {
    let pending = this.loading.get(batch);
    if (pending) return pending;
    const entry = this.manifest?.vehicle_models?.[key];
    if (!entry) return Promise.reject(new Error(`Missing parked car model ${key}`));
    pending = new GLTFLoader().loadAsync(new URL(entry.url, this.manifest!.baseUrl).href).then(gltf => {
      let mesh: THREE.Mesh | undefined;
      gltf.scene.traverse(o => { if ((o as THREE.Mesh).isMesh) mesh = o as THREE.Mesh; });
      if (!mesh) throw new Error(`Parked car model ${key} has no mesh`);
      if (recolourable(batch)) mesh = new THREE.Mesh(mesh.geometry, paintMaterial(mesh.material as THREE.MeshStandardMaterial, batch));
      this.sources.set(batch, mesh);
      return mesh;
    });
    pending.catch(() => this.loading.delete(batch));
    this.loading.set(batch, pending);
    return pending;
  }

  /** A standalone draw of a driven or remote car with its own paint. */
  private actor(batch: string, color: string, name: string): THREE.Mesh {
    const source = this.sources.get(batch)!;
    const actor = new THREE.Mesh(source.geometry, source.material);
    if (recolourable(batch)) paintActor(actor, color);
    actor.name = name;
    actor.castShadow = true;
    actor.receiveShadow = true;
    return actor;
  }

  /** Ambient traffic draws up to `count` cars of each recolourable body in that body's parked batch, so moving
   *  cars add no draw, material or pipeline. Takes effect at the next load. */
  reserveTraffic(count: number) { this.trafficReserve = Math.max(0, count); }

  /** Body models that ambient traffic can draw now (a resident GLB of the body exists). */
  trafficModels(): string[] { return TRAFFIC_MODELS.filter(model => this.groups.has(model)); }

  /** This frame's moving cars; drawn by the next update(). Cars of a body without a batch use another body. */
  setTraffic(cars: readonly TrafficInstance[]) {
    for (const list of this.traffic.values()) list.length = 0;
    const fallback = this.trafficModels()[0];
    for (const car of cars) {
      const batch = this.groups.has(car.model) ? car.model : fallback;
      if (!batch) continue;
      let list = this.traffic.get(batch);
      if (!list) this.traffic.set(batch, list = []);
      list.push(car);
    }
  }

  /** The parked batches draw this vehicle record (after a load that included it). */
  renders(id: string) { return [...this.groups.values()].some(({ cars }) => cars.some(c => c.object_id === id)) || this.dynamic.has(id); }

  async load(manifest: Manifest) {
    this.manifest = manifest;
    const cars = (manifest.vehicles ?? []).filter(c => c.model !== 'truck' && !this.dynamic.has(c.object_id));
    const members = new Map<string, ParkedVehicle[]>();
    for (const car of cars) {
      const batch = paintBatch(car.model, car.color);
      members.set(batch, [...members.get(batch) ?? [], car]);
    }
    if (this.trafficReserve) for (const model of TRAFFIC_MODELS) if (!members.has(model) && this.assetKey(model)) members.set(model, []);
    const extra = (batch: string) => (TRAFFIC_MODELS as readonly string[]).includes(batch) ? this.trafficReserve : 0;
    await Promise.all([...members].map(([batch, list]) => this.sources.has(batch) ? null
      : this.source(batch, this.assetKey(batch) ?? `${list[0].model}/${list[0].color}`)));
    for (const [batch, { mesh }] of this.groups) {
      if (members.has(batch)) continue;
      mesh.removeFromParent(); mesh.dispose(); this.groups.delete(batch);
    }
    for (const [batch, list] of members) {
      const source = this.sources.get(batch)!;
      const previous = this.groups.get(batch);
      if (previous && previous.mesh.instanceMatrix.count >= list.length + extra(batch)) {
        previous.cars = list; previous.mesh.count = list.length; previous.trafficPaints = '';
        if (recolourable(batch)) paintInstances(previous.mesh, list.map(c => c.color));
        this.scene.add(previous.mesh);
        continue;
      }
      const capacity = Math.max(16, 2 ** Math.ceil(Math.log2(Math.max(1, list.length + extra(batch)))));
      const mesh = new THREE.InstancedMesh(source.geometry, source.material, capacity);
      if (recolourable(batch)) paintInstances(mesh, list.map(c => c.color));
      mesh.count = list.length;
      list.forEach((_car, i) => mesh.setMatrixAt(i, HIDDEN));
      mesh.name = `parked/${batch}`;
      mesh.castShadow = true; mesh.receiveShadow = true;
      // Instanced node graphs are object-specific even when native pipelines are cached.
      // Keep the old batch resident until all passes and uploads for its replacement are ready.
      try { await this.prepare(mesh); }
      catch (error) { mesh.dispose(); throw error; }
      if (previous) { previous.mesh.removeFromParent(); previous.mesh.dispose(); }
      this.scene.add(mesh);
      this.groups.set(batch, { mesh, cars: list, trafficPaints: '' });
    }
  }

  takeOver(id: string) {
    if (this.dynamic.has(id)) return;
    for (const [batch, { mesh, cars }] of this.groups) {
      const index = cars.findIndex(car => car.object_id === id);
      if (index < 0) continue;
      const actor = this.actor(batch, cars[index].color, `driven/${id}`);
      this.scene.add(actor);
      this.dynamic.set(id, actor);
      cars.splice(index, 1);
      mesh.count = cars.length;
      if (recolourable(batch)) paintInstances(mesh, cars.map(c => c.color));
      // A traffic batch stays in the scene: an empty instanced draw is culled by its empty bounds.
      if (!mesh.count && !this.trafficReserve) mesh.removeFromParent();
      this.groups.get(batch)!.trafficPaints = '';
      mesh.computeBoundingSphere();
      return;
    }
  }

  /** Any paint of a recolourable model supplies its geometry and the shared parts of its texture. */
  private assetKey(batch: string): string | undefined {
    const models = this.manifest?.vehicle_models ?? {};
    return recolourable(batch) ? Object.keys(models).find(key => paintBatch(...key.split('/') as [string, string]) === batch)
      : batch in models ? batch : undefined;
  }

  /** A remote paint that no resident draft uses still draws from any GLB of its body model. */
  requestRemoteModel(key: string) {
    const batch = paintBatch(...key.split('/') as [string, string]);
    const asset = this.assetKey(batch);
    if (!asset || this.sources.has(batch) || this.loading.has(batch)) return;
    this.source(batch, asset).catch(error => console.warn('[remote vehicle model]', key, error));
  }

  /** A remote driver uses the same mesh and material as a local takeover. */
  remoteModel(key: string): THREE.Mesh | null {
    const [model, color] = key.split('/');
    const batch = paintBatch(model, color);
    return this.sources.has(batch) ? this.actor(batch, color, `remote/${key}`) : null;
  }

  /** Bodies follow their physics state; the others rest where `rest` places them, or stay hidden. */
  update(states: VehicleState[], rest?: ParkedRest) {
    const byId = new Map(states.map(s => [s.id, s]));
    for (const [batch, group] of this.groups) {
      const { mesh, cars } = group;
      cars.forEach((car, i) => {
        const pose = byId.get(car.object_id) ?? rest?.restingPose(car);
        if (!pose) { mesh.setMatrixAt(i, HIDDEN); return; }
        this.position.set(pose.pos.x, pose.pos.y - 0.75, pose.pos.z);
        this.rotation.set(pose.rot.x, pose.rot.y, pose.rot.z, pose.rot.w).multiply(LOCAL_AXIS);
        mesh.setMatrixAt(i, this.matrix.compose(this.position, this.rotation, this.scale));
      });
      // Moving traffic follows the parked instances in the same draw.
      const moving = this.traffic.get(batch) ?? [];
      const count = Math.min(moving.length, mesh.instanceMatrix.count - cars.length);
      for (let i = 0; i < count; i++) {
        const car = moving[i];
        this.position.set(car.x, car.y - CHASSIS_LIFT, car.z);
        this.rotation.set(car.q.x, car.q.y, car.q.z, car.q.w).multiply(LOCAL_AXIS);
        mesh.setMatrixAt(cars.length + i, this.matrix.compose(this.position, this.rotation, this.scale));
      }
      if (this.trafficReserve) {
        mesh.count = cars.length + count;
        const paints = `${cars.length}|${moving.slice(0, count).map(c => c.color).join(',')}`;
        if (count && paints !== group.trafficPaints && recolourable(batch)) paintInstances(mesh, moving.slice(0, count).map(c => c.color), cars.length);
        group.trafficPaints = paints;
      }
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
    }
    for (const [id, actor] of this.dynamic) {
      const state = byId.get(id);
      actor.visible = !!state;
      if (!state) continue;
      actor.position.set(state.pos.x, state.pos.y - 0.75, state.pos.z);
      actor.quaternion.set(state.rot.x, state.rot.y, state.rot.z, state.rot.w).multiply(LOCAL_AXIS);
    }
  }

  inspect(id: string, camera: THREE.Camera) {
    const actor = this.dynamic.get(id);
    if (!actor) return null;
    camera.updateMatrixWorld();
    actor.updateMatrixWorld();
    const frustum = new THREE.Frustum().setFromProjectionMatrix(
      new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    const projected = actor.position.clone().project(camera);
    return { parented: actor.parent === this.scene, visible: actor.visible && frustum.intersectsObject(actor),
      position: actor.position.toArray(), projected: [projected.x, projected.y, projected.z], castShadow: actor.castShadow,
      staticInstances: [...this.groups.values()].reduce((count, { cars }) =>
        count + cars.filter(car => car.object_id === id).length, 0) };
  }
}
