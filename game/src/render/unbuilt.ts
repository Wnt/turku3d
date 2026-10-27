// The unbuilt look: far LOD (500 m and 1 km tiles) drawn where no playable tile is (no published L0 tile and no
// drawn draft) has no buildings to touch, no props and at most a ring of ground collision, so it is drawn flat grey
// with faint stripes. The built area keeps its colours, and the edge of what can be played reads as an edge
// (world/README.md "Collision edge and draft tiles"). A 250 m mask texture over the L0 grid drives it.
import * as THREE from 'three/webgpu';
import { float, fract, materialColor, mix, positionWorld, step, texture, vec2, vec3, dot } from 'three/tsl';
import { VISUAL_L0 } from '../core/origin';

/** L0 cells per mask side (256 x 250 m = 64 km, centred on the origin); beyond it everything is unbuilt. */
const SIZE = 256;

export class UnbuiltArea {
  private data = new Uint8Array(SIZE * SIZE);
  readonly mask = new THREE.DataTexture(this.data, SIZE, SIZE, THREE.RedFormat, THREE.UnsignedByteType);
  private owned = new Map<THREE.Object3D, { meshes: { mesh: THREE.Mesh; source: THREE.Material | THREE.Material[] }[]; styled: THREE.Material[] }>();
  private key = '';

  constructor(private renderer: THREE.WebGPURenderer) {
    this.mask.magFilter = this.mask.minFilter = THREE.NearestFilter;
    this.mask.generateMipmaps = false;
    this.mask.needsUpdate = true;
  }

  /** The L0 tiles drawn at full detail (published or drafts); every other cell is unbuilt. */
  setBuilt(ids: Iterable<string>) {
    const cells = [...ids].flatMap(id => {
      const m = /^t0_(-?\d+)_(-?\d+)$/.exec(id);
      if (!m) return [];
      const i = +m[1] + SIZE / 2, j = +m[2] + SIZE / 2;
      return i >= 0 && j >= 0 && i < SIZE && j < SIZE ? [j * SIZE + i] : [];
    }).sort((a, b) => a - b);
    const key = cells.join(',');
    if (key === this.key) return;
    this.key = key;
    this.data.fill(0);
    for (const k of cells) this.data[k] = 255;
    this.mask.needsUpdate = true;
  }

  /** Whether (x, z) lies in a built L0 tile (tests and the HUD). */
  built(x: number, z: number): boolean {
    const i = Math.floor(x / VISUAL_L0) + SIZE / 2, j = Math.floor(z / VISUAL_L0) + SIZE / 2;
    return i >= 0 && j >= 0 && i < SIZE && j < SIZE && this.data[j * SIZE + i] > 0;
  }

  /** Far LOD tile models (`level` 1 and up) get the unbuilt look outside the built cells. */
  apply(model: THREE.Object3D, tileId: string | undefined) {
    if (!tileId || /^t0_/.test(tileId)) return;
    const meshes: { mesh: THREE.Mesh; source: THREE.Material | THREE.Material[] }[] = [];
    const cache = new Map<THREE.Material, THREE.Material>();
    const style = (m: THREE.Material) => { if (!cache.has(m)) cache.set(m, this.style(m)); return cache.get(m)!; };
    model.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      meshes.push({ mesh, source: mesh.material });
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
    });
    this.owned.set(model, { meshes, styled: [...cache.values()] });
  }

  release(model: THREE.Object3D) {
    const owned = this.owned.get(model);
    if (!owned) return;
    this.owned.delete(model);
    for (const { mesh, source } of owned.meshes) mesh.material = source;
    for (const m of owned.styled) m.dispose();
  }

  private style(source: THREE.Material): THREE.Material {
    const node = ((source as THREE.NodeMaterial).isNodeMaterial ? source.clone()
      : this.renderer.library.fromMaterial(source.clone())) as THREE.MeshStandardNodeMaterial;
    const base = ((node.colorNode as typeof materialColor | null) ?? materialColor).rgb;
    const uv = positionWorld.xz.div(VISUAL_L0).add(SIZE / 2).div(SIZE);
    const built = texture(this.mask, vec2(uv.x, uv.y)).r;
    // Grey at 70 % of the luminance, with faint diagonal stripes 8 m apart in world space.
    const stripes = step(0.5, fract(positionWorld.x.add(positionWorld.z).div(8))).mul(0.06).add(0.94);
    const grey = vec3(dot(base, vec3(0.2126, 0.7152, 0.0722)).mul(0.7).add(0.05)).mul(stripes);
    node.colorNode = mix(grey, base, float(built));
    return node;
  }

  dispose() {
    for (const model of [...this.owned.keys()]) this.release(model);
    this.mask.dispose();
  }
}
