import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { Environment } from '../../src/render/environment';
import { MaterialStyler } from '../../src/render/materials';

/** Building models each bring their own copy of an identical roof material; static batching needs one object. */
test('identical unstyled materials share one material across models and tiles; different ones stay apart', () => {
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(new THREE.Scene(), renderer, 'medium');
  const styler = new MaterialStyler(renderer, env, 'medium', true);
  const blockCrop = new THREE.Texture(), ownCrop = new THREE.Texture();
  const roof = (map: THREE.Texture) => {
    const material = new THREE.MeshStandardMaterial({ map, roughness: 0.85 });
    material.userData.material_key = 'roof_ortho';
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(), material); mesh.userData.layer = 'buildings';
    return mesh;
  };
  const tile = () => { const model = new THREE.Group(); model.add(roof(blockCrop), roof(blockCrop), roof(ownCrop)); return model; };
  const a = tile(), b = tile();
  const sources = a.children.map(m => (m as THREE.Mesh).material);
  styler.apply(a); styler.apply(b);
  const [first, second, own] = a.children.map(m => (m as THREE.Mesh).material as THREE.MeshStandardMaterial);
  expect(first).toBe(second); expect(own).not.toBe(first);
  expect(first.map).toBe(blockCrop); expect(own.map).toBe(ownCrop);
  expect((b.children[0] as THREE.Mesh).material).toBe(first);
  styler.release(a);
  expect(a.children.map(m => (m as THREE.Mesh).material)).toEqual(sources);
  expect((b.children[0] as THREE.Mesh).material).toBe(first);
  styler.dispose(); env.dispose();
});
