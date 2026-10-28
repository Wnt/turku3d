import type * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type { GLTFLoaderPlugin } from 'three/addons/loaders/GLTFLoader.js';
import { UploadBudget } from './uploadBudget';

/** Includes TSL array maps as well as ordinary glTF PBR slots. */
export function modelTextures(scene: THREE.Object3D): Set<THREE.Texture> {
  const textures = new Set<THREE.Texture>();
  scene.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
        else if (value?.isNode) (value as Node).traverse(node => {
          const map = (node as Node & { value?: THREE.Texture }).value;
          if (map?.isTexture) textures.add(map);
        });
      }
    }
  });
  return textures;
}

const queues = new WeakMap<THREE.WebGPURenderer, TextureUploads>();
export function textureUploads(renderer: THREE.WebGPURenderer): TextureUploads {
  let queue = queues.get(renderer);
  if (!queue) { queue = new TextureUploads(renderer); queues.set(renderer, queue); }
  return queue;
}

class TextureUploads {
  private budget = new UploadBudget();
  private scheduled = false;
  private versions = new WeakMap<THREE.Texture, { version: number; done: Promise<void> }>();
  constructor(private renderer: THREE.WebGPURenderer) {}
  enqueue(texture: THREE.Texture): Promise<void> {
    const previous = this.versions.get(texture);
    if (previous?.version === texture.version) return previous.done;
    const mipmaps = texture.mipmaps as { data?: { byteLength: number } }[];
    const image = texture.image as { data?: ArrayBufferView; width?: number; height?: number } | undefined;
    const bytes = mipmaps?.reduce((sum, mip) => sum + (mip.data?.byteLength ?? 0), 0)
      || image?.data?.byteLength || 4 * (image?.width ?? 1) * (image?.height ?? 1);
    const done = this.budget.enqueue(bytes, () => this.renderer.initTexture(texture));
    this.versions.set(texture, { version: texture.version, done });
    this.schedule();
    return done;
  }
  private schedule() {
    if (this.scheduled) return;
    this.scheduled = true;
    requestAnimationFrame(() => {
      this.scheduled = false;
      this.budget.drain();
      if (this.budget.pending) this.schedule();
    });
  }
  /** Runs after glTF assigns colour space, UV transforms and samplers, before the model can be drawn. */
  plugin(): GLTFLoaderPlugin {
    return { name: 'TURKU_texture_upload_budget', afterRoot: async result => {
      await Promise.all([...modelTextures(result.scene)].map(texture => this.enqueue(texture)));
    } };
  }
}
