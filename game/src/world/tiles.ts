// Visual tiles via 3DTilesRendererJS. tileset.json is in the 3D Tiles Z-up frame (docs/specs/tiles.md §1);
// the renderer rotates Y-up glTF content to Z-up, so the group gets rotation.x = -PI/2 to return to engine axes.
import * as THREE from 'three/webgpu';
import { TilesRenderer } from '3d-tiles-renderer';
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/plugins';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

export class VisualTiles {
  readonly tiles: TilesRenderer;
  residentTris = 0;
  residentModels = 0;
  loadedModels = 0;
  errors: string[] = [];
  private triCount = new Map<THREE.Object3D, number>();
  private resolution = new THREE.Vector2();

  constructor(url: string, private renderer: THREE.WebGPURenderer, camera: THREE.PerspectiveCamera, scene: THREE.Scene) {
    const ktx2 = new KTX2Loader().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
    this.tiles = new TilesRenderer(url);
    this.tiles.registerPlugin(new GLTFExtensionsPlugin({ ktxLoader: ktx2, meshoptDecoder: MeshoptDecoder }));
    this.tiles.errorTarget = 8;
    this.tiles.group.rotation.x = -Math.PI / 2;
    this.tiles.setCamera(camera);
    renderer.getDrawingBufferSize(this.resolution);
    this.tiles.setResolution(camera, this.resolution.x, this.resolution.y);
    this.tiles.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => {
      let tris = 0;
      model.traverse((o) => {
        const m = o as THREE.Mesh;
        if (!m.isMesh) return;
        m.castShadow = true; m.receiveShadow = true;
        const g = m.geometry;
        tris += (g.index ? g.index.count : g.attributes.position.count) / 3;
      });
      this.triCount.set(model, tris);
      this.residentTris += tris; this.residentModels++; this.loadedModels++;
    });
    this.tiles.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => {
      this.residentTris -= this.triCount.get(model) ?? 0; this.residentModels--; this.triCount.delete(model);
    });
    this.tiles.addEventListener('load-error', ({ error, url: u }: { error: Error; url: string | URL }) => {
      const msg = `tile load error ${String(u)}: ${error?.message ?? error}`;
      this.errors.push(msg); console.error(msg);
    });
    scene.add(this.tiles.group);
  }

  update(camera: THREE.PerspectiveCamera) {
    camera.updateMatrixWorld();
    // SSE is measured in actual output pixels on every backend, including HiDPI and capped render sizes.
    this.renderer.getDrawingBufferSize(this.resolution);
    this.tiles.setResolution(camera, this.resolution.x, this.resolution.y);
    this.tiles.update();
  }

  /** True when the tileset has loaded something and nothing is pending for the current view. */
  get settled(): boolean {
    return this.tiles.root !== null && this.loadedModels > 0 && this.tiles.loadProgress >= 1;
  }
}
