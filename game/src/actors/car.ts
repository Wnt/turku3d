import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CAR } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import type { Manifest, ParkedVehicle } from '../world/manifest';

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

/** Parked cars share draws until one is driven; driven cars keep their own scene-level mesh. */
export class ParkedCarVisuals {
  private manifest: Manifest | null = null;
  private remoteLoads = new Set<string>();
  private groups = new Map<string, { mesh: THREE.InstancedMesh; cars: ParkedVehicle[] }>();
  private sources = new Map<string, THREE.Mesh>();
  private dynamic = new Map<string, THREE.Mesh>();
  private matrix = new THREE.Matrix4();
  private position = new THREE.Vector3();
  private rotation = new THREE.Quaternion();
  private scale = new THREE.Vector3(1, 1, 1);

  constructor(private readonly scene: THREE.Object3D,
    private readonly prepare: (model: THREE.Object3D) => Promise<void> = async () => {}) {}

  async load(manifest: Manifest) {
    this.manifest = manifest;
    const cars = (manifest.vehicles ?? []).filter(c => c.model !== 'truck' && !this.dynamic.has(c.object_id));
    const loader = new GLTFLoader();
    await Promise.all([...new Set(cars.map(c => `${c.model}/${c.color}`))].map(async key => {
      if (this.sources.has(key)) return;
      const entry = manifest.vehicle_models?.[key];
      if (!entry) throw new Error(`Missing parked car model ${key}`);
      const gltf = await loader.loadAsync(new URL(entry.url, manifest.baseUrl).href);
      let source: THREE.Mesh | undefined;
      gltf.scene.traverse(o => { if ((o as THREE.Mesh).isMesh) source = o as THREE.Mesh; });
      if (!source) throw new Error(`Parked car model ${key} has no mesh`);
      this.sources.set(key, source);
    }));
    const keys = new Set(cars.map(c => `${c.model}/${c.color}`));
    for (const [key, { mesh }] of this.groups) {
      if (keys.has(key)) continue;
      mesh.removeFromParent(); mesh.dispose(); this.groups.delete(key);
    }
    for (const key of keys) {
      const source = this.sources.get(key)!;
      const members = cars.filter(c => `${c.model}/${c.color}` === key);
      const previous = this.groups.get(key);
      if (previous && previous.mesh.instanceMatrix.count >= members.length) {
        previous.cars = members; previous.mesh.count = members.length;
        this.scene.add(previous.mesh);
        continue;
      }
      const capacity = Math.max(16, 2 ** Math.ceil(Math.log2(members.length)));
      const mesh = new THREE.InstancedMesh(source.geometry, source.material, capacity);
      mesh.count = members.length;
      const hidden = new THREE.Matrix4().makeScale(0, 0, 0);
      members.forEach((_car, i) => mesh.setMatrixAt(i, hidden));
      mesh.name = `parked/${key}`;
      mesh.castShadow = true; mesh.receiveShadow = true;
      // Instanced node graphs are object-specific even when native pipelines are cached.
      // Keep the old batch resident until all colour passes and uploads for its replacement are ready.
      try { await this.prepare(mesh); }
      catch (error) { mesh.dispose(); throw error; }
      if (previous) { previous.mesh.removeFromParent(); previous.mesh.dispose(); }
      this.scene.add(mesh);
      this.groups.set(key, { mesh, cars: members });
    }
  }

  takeOver(id: string) {
    if (this.dynamic.has(id)) return;
    for (const { mesh, cars } of this.groups.values()) {
      const index = cars.findIndex(car => car.object_id === id);
      if (index < 0) continue;
      const actor = new THREE.Mesh(mesh.geometry, mesh.material);
      actor.name = `driven/${id}`;
      actor.castShadow = true;
      actor.receiveShadow = true;
      this.scene.add(actor);
      this.dynamic.set(id, actor);
      cars.splice(index, 1);
      mesh.count = cars.length;
      if (!mesh.count) mesh.removeFromParent();
      mesh.computeBoundingSphere();
      return;
    }
  }

  requestRemoteModel(key: string) {
    const entry = this.manifest?.vehicle_models?.[key];
    if (!entry || this.sources.has(key) || this.remoteLoads.has(key)) return;
    this.remoteLoads.add(key);
    void new GLTFLoader().loadAsync(new URL(entry.url, this.manifest!.baseUrl).href).then(gltf => {
      gltf.scene.traverse(o => { if ((o as THREE.Mesh).isMesh) this.sources.set(key, o as THREE.Mesh); });
    }).catch(error => console.warn('[remote vehicle model]', key, error));
  }

  /** A remote driver uses the same mesh and material as a local takeover. */
  remoteModel(key: string): THREE.Mesh | null {
    const source = this.sources.get(key);
    if (!source) return null;
    const model = new THREE.Mesh(source.geometry, source.material);
    model.name = `remote/${key}`;
    model.castShadow = true;
    model.receiveShadow = true;
    return model;
  }

  update(states: VehicleState[]) {
    const byId = new Map(states.map(s => [s.id, s]));
    for (const { mesh, cars } of this.groups.values()) {
      cars.forEach((car, i) => {
        const state = byId.get(car.object_id);
        if (!state) { mesh.setMatrixAt(i, this.matrix.makeScale(0, 0, 0)); return; }
        this.position.set(state.pos.x, state.pos.y - 0.75, state.pos.z);
        this.rotation.set(state.rot.x, state.rot.y, state.rot.z, state.rot.w)
          .multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2));
        mesh.setMatrixAt(i, this.matrix.compose(this.position, this.rotation, this.scale));
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
    }
    for (const [id, actor] of this.dynamic) {
      const state = byId.get(id);
      actor.visible = !!state;
      if (!state) continue;
      actor.position.set(state.pos.x, state.pos.y - 0.75, state.pos.z);
      actor.quaternion.set(state.rot.x, state.rot.y, state.rot.z, state.rot.w)
        .multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2));
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
