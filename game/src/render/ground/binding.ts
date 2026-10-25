import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { loadGroundLibrary, loadGroundTile } from './material';

/** Only assembler-declared URLs are requested; unbound tiles retain their source materials. */
export class GroundBindings {
  pending = 0;
  readonly errors: string[] = [];
  private loader: KTX2Loader;
  private libraries = new Map<string, ReturnType<typeof loadGroundLibrary>>();
  private tiles = new Map<THREE.Object3D, Awaited<ReturnType<typeof loadGroundTile>> | null>();
  constructor(renderer: THREE.WebGPURenderer, private base: string) {
    this.loader = new KTX2Loader().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
  }
  async apply(model: THREE.Object3D) {
    let binding: { spec: string; library: string } | undefined;
    model.traverse(node => { binding ??= node.userData.ground_binding; });
    if (!binding) return;
    this.tiles.set(model, null);
    this.pending++;
    try {
      const libraryURL = new URL(binding.library, this.base).href;
      let library = this.libraries.get(libraryURL);
      if (!library) {
        library = loadGroundLibrary(libraryURL, this.loader);
        this.libraries.set(libraryURL, library);
      }
      const tile = await loadGroundTile(new URL(binding.spec, this.base).href, this.loader, await library);
      if (!this.tiles.has(model)) { tile.dispose(); return; }
      this.tiles.set(model, tile);
      model.traverse(node => {
        const mesh = node as THREE.Mesh;
        if (!mesh.isMesh) return;
        let ground = false, water = false;
        for (let parent: THREE.Object3D | null = mesh; parent; parent = parent.parent) {
          if (parent.userData.layer === 'ground') ground = true;
          const key = String(parent.userData.material_key ?? '').toLowerCase();
          if (key.split(/[^a-z0-9]+/).some(word => word === 'water' || word === 'river')) water = true;
        }
        if (ground && !water) mesh.material = tile.material;
      });
    } catch (error) {
      const message = `ground binding: ${String(error)}`;
      this.errors.push(message);
      console.error(message);
    } finally { this.pending--; }
  }
  release(model: THREE.Object3D) {
    this.tiles.get(model)?.dispose();
    this.tiles.delete(model);
  }
  dispose() {
    for (const model of this.tiles.keys()) this.release(model);
    for (const library of this.libraries.values()) {
      void library.then(maps => Object.values(maps).forEach(map => map.dispose()), () => {});
    }
    this.libraries.clear();
    this.loader.dispose();
  }
}
