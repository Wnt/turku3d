import * as THREE from 'three/webgpu';
import { textureUploads } from '../textureUploads';
import { LoadingKtx } from '../../world/loadingKtx';
import { loadGroundLibrary, loadGroundTile } from './material';

/** Only assembler-declared URLs are requested; unbound tiles retain their source materials. */
export class GroundBindings {
  pending = 0;
  readonly errors: string[] = [];
  private loader: LoadingKtx;
  private libraries = new Map<string, ReturnType<typeof loadGroundLibrary>>();
  private tiles = new Map<THREE.Object3D, Awaited<ReturnType<typeof loadGroundTile>> | null>();
  private batches = new Map<THREE.Object3D, { mesh: THREE.Mesh; sources: THREE.Mesh[] }>();
  constructor(private renderer: THREE.WebGPURenderer, private base: string) {
    this.loader = new LoadingKtx().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
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
      await Promise.all([...Object.values(tile.maps), ...Object.values(await library)].map(map => textureUploads(this.renderer).enqueue(map)));
      if (!this.tiles.has(model)) { tile.dispose(); return; }
      this.tiles.set(model, tile);
      const sources: THREE.Mesh[] = [];
      model.traverse(node => {
        const mesh = node as THREE.Mesh;
        if (!mesh.isMesh) return;
        let ground = false, water = false;
        for (let parent: THREE.Object3D | null = mesh; parent; parent = parent.parent) {
          if (parent.userData.layer === 'ground') ground = true;
          const key = String(parent.userData.material_key ?? '').toLowerCase();
          if (key.split(/[^a-z0-9]+/).some(word => word === 'water' || word === 'river')) water = true;
        }
        if (ground && !water) {
          mesh.material = tile.material;
          sources.push(mesh);
        }
      });
      if (sources.length > 1) this.batch(model, sources, tile.material);
    } catch (error) {
      const message = `ground binding: ${String(error)}`;
      this.errors.push(message);
      console.error(message);
    } finally { this.pending--; }
  }
  release(model: THREE.Object3D) {
    const batch = this.batches.get(model);
    if (batch) {
      batch.mesh.removeFromParent();
      batch.mesh.geometry.dispose();
      for (const source of batch.sources) source.visible = true;
      this.batches.delete(model);
    }
    this.tiles.get(model)?.dispose();
    this.tiles.delete(model);
  }
  private batch(model: THREE.Object3D, sources: THREE.Mesh[], material: THREE.Material) {
    model.updateWorldMatrix(true, true);
    const inverse = model.matrixWorld.clone().invert();
    const position = new THREE.Vector3();
    const normal = new THREE.Vector3();
    const vertices: number[] = [], normals: number[] = [], indices: number[] = [];
    for (const mesh of sources) {
      if ((mesh as THREE.InstancedMesh).isInstancedMesh) return;
      const geometry = mesh.geometry;
      const points = geometry.getAttribute('position');
      if (!points) return;
      const directions = geometry.getAttribute('normal');
      const matrix = inverse.clone().multiply(mesh.matrixWorld);
      const normalMatrix = new THREE.Matrix3().getNormalMatrix(matrix);
      const offset = vertices.length / 3;
      for (let i = 0; i < points.count; i++) {
        position.fromBufferAttribute(points, i).applyMatrix4(matrix);
        vertices.push(position.x, position.y, position.z);
        if (directions) normal.fromBufferAttribute(directions, i).applyMatrix3(normalMatrix).normalize();
        else normal.set(0, 1, 0);
        normals.push(normal.x, normal.y, normal.z);
      }
      const index = geometry.getIndex();
      if (index) for (let i = 0; i < index.count; i++) indices.push(offset + index.getX(i));
      else for (let i = 0; i < points.count; i++) indices.push(offset + i);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setIndex(indices);
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = 'ground-batch';
    mesh.userData.layer = 'ground';
    mesh.receiveShadow = true;
    mesh.castShadow = false;
    for (const source of sources) source.visible = false;
    model.add(mesh);
    this.batches.set(model, { mesh, sources });
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
