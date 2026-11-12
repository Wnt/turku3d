import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { textureUploads } from '../../src/render/textureUploads';
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js';

test('failed glTF upload waits for sibling uploads before releasing material resources', async () => {
  const first = new THREE.Texture(), second = new THREE.Texture();
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial({ map: first, normalMap: second });
  root.add(new THREE.Mesh(geometry, material));
  const disposed = vi.fn(); geometry.addEventListener('dispose', disposed);
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  const uploads = textureUploads({} as THREE.WebGPURenderer);
  vi.spyOn(uploads, 'enqueue').mockImplementation(texture => texture === first ? Promise.reject(new Error('upload failed')) : pending);
  const plugin = uploads.plugin(), job = Promise.resolve(plugin.afterRoot!({ scene: root } as GLTF));
  await Promise.resolve(); await Promise.resolve(); expect(disposed).not.toHaveBeenCalled();
  finish(); await expect(job).rejects.toThrow('upload failed'); expect(disposed).toHaveBeenCalledOnce();
});
