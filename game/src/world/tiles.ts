// Resident draft model ownership and telemetry; loading belongs to CoarseTiles.
import * as THREE from 'three/webgpu';
import { batchTileShadows, prepareTileShadows } from './shadowBatch';
import { disposeShadowBatch } from './shadowChunks';

type ModelEvent = { scene: THREE.Object3D; tile: { extras?: Record<string, unknown> } };
interface TileEvents { 'load-model': ModelEvent; 'dispose-model': ModelEvent }

export class VisualTiles {
  readonly events = new THREE.EventDispatcher<TileEvents>();
  readonly modelEvents = this.events;
  readonly models = new Set<THREE.Object3D>();
  residentTris = 0;
  residentModels = 0;
  loadedModels = 0;
  private counts = new Map<THREE.Object3D, number>();
  private shadows = new Map<THREE.Object3D, THREE.Mesh>();
  private prepared = new WeakSet<THREE.Object3D>();

  constructor(private idPass = false) {}

  /** Final shader-affecting flags and derived geometry precede GPU preparation, while the model is hidden. */
  prepareModel(scene: THREE.Object3D) {
    if (this.prepared.has(scene)) return;
    scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (mesh.isMesh) { mesh.castShadow = true; mesh.receiveShadow = true; }
    });
    if (!this.idPass) {
      const shadow = batchTileShadows(scene);
      if (shadow) this.shadows.set(scene, shadow);
    }
    this.prepared.add(scene);
  }

  /** Streaming keeps shadow buffer construction within the preparation frame budget. */
  async prepareModelAsync(scene: THREE.Object3D) {
    if (this.prepared.has(scene)) return;
    scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (mesh.isMesh) { mesh.castShadow = true; mesh.receiveShadow = true; }
    });
    if (!this.idPass) {
      const shadow = await prepareTileShadows(scene);
      if (shadow) this.shadows.set(scene, shadow);
    }
    this.prepared.add(scene);
  }

  /** Also releases derived resources if preparation is cancelled before publication. */
  releasePreparedModel(scene: THREE.Object3D) {
    const shadow = this.shadows.get(scene);
    if (shadow) { disposeShadowBatch(shadow); this.shadows.delete(scene); }
    this.prepared.delete(scene);
  }

  trackModel(scene: THREE.Object3D, id: string) {
    if (this.models.has(scene)) return;
    this.prepareModel(scene);
    let tris = 0;
    scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (mesh.isMesh && !mesh.userData.shadowBatch)
        tris += (mesh.geometry.index?.count ?? mesh.geometry.attributes.position.count) / 3;
    });
    this.models.add(scene); this.counts.set(scene, tris);
    this.residentTris += tris; this.residentModels++; this.loadedModels++;
    this.events.dispatchEvent({ type: 'load-model', scene, tile: { extras: { id } } });
  }

  untrackModel(scene: THREE.Object3D, id: string) {
    if (!this.models.delete(scene)) return;
    this.releasePreparedModel(scene);
    this.residentTris -= this.counts.get(scene) ?? 0; this.counts.delete(scene);
    this.residentModels--;
    this.events.dispatchEvent({ type: 'dispose-model', scene, tile: { extras: { id } } });
  }
}
