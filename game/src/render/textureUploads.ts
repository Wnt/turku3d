import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type { GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import { UploadBudget } from './uploadBudget';
import { shareModelTextures } from '../world/sharedAssets';
import { guardReleasedTextures } from '../world/loadingKtx';
import { afterRender } from './afterRender';
import { streamTrace } from '../telemetry/streamTrace';

/** Includes TSL array maps as well as ordinary glTF PBR slots. */
export function modelTextures(scene: THREE.Object3D, camera?: THREE.Camera): Set<THREE.Texture> {
  let frustum: THREE.Frustum | undefined;
  if (camera) {
    scene.updateWorldMatrix(true, true);
    camera.updateWorldMatrix(true, false);
    frustum = new THREE.Frustum().setFromProjectionMatrix(
      new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse),
      camera.coordinateSystem, camera.reversedDepth);
  }
  const textures = new Set<THREE.Texture>();
  const visited = new Set<THREE.Material>();
  const roots = new Set<Node>();
  const visit = (object: THREE.Object3D) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || (camera && (!mesh.layers.test(camera.layers)
      || (mesh.frustumCulled && !frustum!.intersectsObject(mesh))))) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (visited.has(material)) continue;
      visited.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
        else if (value?.isNode) roots.add(value as Node);
      }
    }
  };
  if (camera) scene.traverseVisible(visit); else scene.traverse(visit);
  for (const root of roots) for (const holder of textureHolders(root)) if (holder.value?.isTexture) textures.add(holder.value);
  return textures;
}

type Holder = Node & { value?: THREE.Texture; isTextureNode?: boolean };
/** Texture nodes under each material node already walked. Tile materials are shared across tiles
 *  (render/materials.ts), and re-walking their graphs for every prepared tile was 2.8 s of a 6.7-min Windows
 *  drive (2.4 s in Node.getChildren; win-drive-2). Each node's texture is read when asked: a swapped `value` counts. */
const holdersByRoot = new WeakMap<Node, Holder[]>();
function textureHolders(root: Node): Holder[] {
  let holders = holdersByRoot.get(root);
  if (holders) return holders;
  holders = [];
  // TSL is a shared graph, not a tree. Node.traverse revisits every path through
  // common expressions (exponential for ground shaders), even across material slots.
  const nodes = new Set<Node>(), pending = [root];
  while (pending.length) {
    const node = pending.pop()! as Holder;
    if (nodes.has(node)) continue;
    nodes.add(node);
    if (node.isTextureNode || node.value?.isTexture) holders.push(node);
    for (const child of node.getChildren()) if (!nodes.has(child)) pending.push(child);
  }
  holdersByRoot.set(root, holders);
  return holders;
}

const queues = new WeakMap<THREE.WebGPURenderer, TextureUploads>();
/** Whether `texture` went through the upload queue at its current version (its GPU copy is current). */
export function textureUploaded(renderer: THREE.WebGPURenderer, texture: THREE.Texture) {
  return queues.get(renderer)?.uploadedAt(texture) === texture.version;
}
export function textureUploads(renderer: THREE.WebGPURenderer): TextureUploads {
  let queue = queues.get(renderer);
  if (!queue) { queue = new TextureUploads(renderer); queues.set(renderer, queue); }
  return queue;
}

class TextureUploads {
  private budget = new UploadBudget();
  private scheduled = false;
  private versions = new WeakMap<THREE.Texture, { version: number; done: Promise<void> }>();
  private uploaded = new WeakMap<THREE.Texture, number>();
  constructor(private renderer: THREE.WebGPURenderer) {
    // Released texture mips are never uploaded (world/loadingKtx.ts).
    guardReleasedTextures(renderer as unknown as Parameters<typeof guardReleasedTextures>[0]);
  }
  uploadedAt(texture: THREE.Texture) { return this.uploaded.get(texture); }
  private init(texture: THREE.Texture) {
    this.renderer.initTexture(texture);
    this.uploaded.set(texture, texture.version);
  }
  enqueue(texture: THREE.Texture): Promise<void> {
    const previous = this.versions.get(texture);
    if (previous?.version === texture.version) return previous.done;
    const mipmaps = texture.mipmaps as { data?: { byteLength: number } }[];
    const image = texture.image as { data?: ArrayBufferView; width?: number; height?: number } | undefined;
    const bytes = mipmaps?.reduce((sum, mip) => sum + (mip.data?.byteLength ?? 0), 0)
      || image?.data?.byteLength || 4 * (image?.width ?? 1) * (image?.height ?? 1);
    const done = this.budget.enqueue(bytes, () => this.init(texture));
    this.versions.set(texture, { version: texture.version, done });
    this.schedule();
    return done;
  }
  /** Changes a texture and uploads it in one budgeted task after a frame: no frame sees the change before its upload
   * (it would upload inside the frame) or draws a half-written texture. */
  update(texture: THREE.Texture, bytes: number, change: () => void): Promise<void> {
    const done = this.budget.enqueue(bytes, () => {
      change();
      this.init(texture);
      this.versions.set(texture, { version: texture.version, done });
    });
    this.schedule();
    return done;
  }
  private schedule() {
    if (this.scheduled) return;
    this.scheduled = true;
    afterRender(() => {
      this.scheduled = false;
      const before = this.budget.pending;
      const started = streamTrace.on ? performance.now() : 0;
      this.budget.drain();
      if (started) {
        const ms = performance.now() - started;
        streamTrace.slice('upload', ms);
        if (before !== this.budget.pending) streamTrace.event('gfx.upload', undefined, { textures: before - this.budget.pending, left: this.budget.pending, ms });
      }
      if (this.budget.pending) this.schedule();
    });
  }
  /** Runs after glTF assigns colour space, UV transforms and samplers, before the model can be drawn. */
  plugin(): GLTFLoaderPlugin {
    return { name: 'TURKU_texture_upload_budget', afterRoot: async result => {
      shareModelTextures(result.scene);
      const started = streamTrace.on ? performance.now() : 0, textures = [...modelTextures(result.scene)];
      await Promise.all(textures.map(texture => this.enqueue(texture)));
      // Install traces split a model read into decode/parse and this budgeted texture upload wait.
      if (streamTrace.on) result.scene.userData.textureUpload = { ms: performance.now() - started, textures: textures.length };
    } };
  }
}
