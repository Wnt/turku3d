import { describe, expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { clusteredTree, crossedTree, VegetationLod } from '../../src/world/vegetationLod';
import { batchTileShadows } from '../../src/world/shadowBatch';

function trees() {
  const model = new THREE.Group();
  const geometry = new THREE.SphereGeometry(1, 24, 16);
  geometry.translate(0, 0.5, 0);
  const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshStandardMaterial(), 60);
  mesh.userData = { layer: 'vegetation', material_key: 'tree_foliage' };
  for (let i = 0; i < mesh.count; i++) mesh.setMatrixAt(i, new THREE.Matrix4().makeTranslation(i * 10 - 300, 0, -100));
  model.add(mesh);
  return { model, mesh, geometry };
}

describe('vegetation LOD', () => {
  it('derives cheaper geometry and a crossed silhouette from the prototype', () => {
    const { geometry } = trees();
    const mid = clusteredTree(geometry), far = crossedTree(geometry);
    expect(mid.index!.count).toBeLessThan(geometry.index!.count);
    expect(far.index!.count / 3).toBe(32);
    far.computeBoundingBox(); geometry.computeBoundingBox();
    expect(far.boundingBox!.min.y).toBeCloseTo(geometry.boundingBox!.min.y);
    expect(far.boundingBox!.max.y).toBeCloseTo(geometry.boundingBox!.max.y);
    expect(Array.from(mid.getAttribute('normal').array).every(Number.isFinite)).toBe(true);
  });

  it('culls cells while keeping at most three draws per prototype, and releases generated resources', () => {
    const { model, mesh } = trees();
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    const lod = new VegetationLod('medium');
    lod.add(model); lod.add(model); lod.update(camera);
    const batches: THREE.InstancedMesh[] = [];
    model.traverse(o => { if ((o as THREE.InstancedMesh).isInstancedMesh && o !== mesh) batches.push(o as THREE.InstancedMesh); });
    expect(mesh.visible).toBe(false);
    expect(batches).toHaveLength(3);
    const visible = batches.filter(m => m.visible);
    expect(visible.length).toBeGreaterThan(0);
    expect(visible.reduce((n, m) => n + m.count, 0)).toBeLessThan(mesh.count);
    expect(batches.slice(1).every(m => !m.castShadow)).toBe(true);
    const versions = batches.map(m => m.instanceMatrix.version);
    lod.update(camera);
    expect(batches.map(m => m.instanceMatrix.version)).toEqual(versions);
    camera.position.set(0, 0, 2000); lod.update(camera);
    expect(batches.every(m => !m.visible)).toBe(true);
    lod.remove(model);
    expect(model.children).toEqual([mesh]); expect(mesh.visible).toBe(true);
  });

  it('uses smaller near-instance budgets at low quality', () => {
    const { model, mesh } = trees();
    const many = new THREE.InstancedMesh(mesh.geometry, mesh.material, 600);
    many.userData = mesh.userData;
    for (let i = 0; i < 600; i++) many.setMatrixAt(i, new THREE.Matrix4().makeTranslation(0, 0, -10));
    model.clear(); model.add(many);
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    const nearCount = (tier: 'low' | 'medium') => {
      const lod = new VegetationLod(tier); lod.add(model); lod.update(camera);
      let count = 0;
      model.traverse(o => { const m = o as THREE.InstancedMesh; if (m.isInstancedMesh && m.visible && m.castShadow) count += m.count; });
      lod.remove(model);
      return count;
    };
    expect(nearCount('low')).toBe(384);
    expect(nearCount('medium')).toBe(600);
  });

  it('never expands vegetation instances into a static tile shadow mesh', () => {
    const { model, mesh } = trees();
    expect(batchTileShadows(model)).toBeNull();
    expect(mesh.castShadow).toBe(false);
  });
});
