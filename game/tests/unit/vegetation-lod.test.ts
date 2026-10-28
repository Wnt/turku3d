import { describe, expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { clusteredTree, crossedTree, VegetationLod } from '../../src/world/vegetationLod';
import { tintDraft } from '../../src/render/facadeKit';
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

  it('shares a cheap clustered crown between mid and far cells', () => {
    const scene = new THREE.Scene(), model = new THREE.Group();
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial(), 2);
    mesh.userData = { layer: 'vegetation', material_key: 'tree_foliage' };
    mesh.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 0, -100));
    mesh.setMatrixAt(1, new THREE.Matrix4().makeTranslation(0, 0, -300));
    model.add(mesh); scene.add(model);
    const lod = new VegetationLod('medium'); lod.add(model);
    lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
    const batches: THREE.InstancedMesh[] = [];
    model.traverse(o => { if ((o as THREE.InstancedMesh).isInstancedMesh && o !== mesh) batches.push(o as THREE.InstancedMesh); });
    expect(batches).toHaveLength(2);
    expect(batches.filter(m => m.visible)).toEqual([batches[1]]);
    expect(batches[1].count).toBe(2);
    expect(batches[1].castShadow).toBe(false);
    expect(batches[1].geometry.index!.count / 3).toBe(12);
    lod.remove(model);
  });

  it('combines matching opaque bark/crown placements while preserving their colours', () => {
    const model = new THREE.Group(), scene = new THREE.Scene(); scene.add(model);
    const bark = new THREE.MeshStandardMaterial({ color: 0x442211 });
    const leaf = new THREE.MeshStandardMaterial({ color: 0xcc9900 });
    const parts = [bark, leaf].map((material, i) => {
      const mesh = new THREE.InstancedMesh(i ? new THREE.SphereGeometry(1, 12, 8) : new THREE.BoxGeometry(), material, 1);
      mesh.userData = { layer: 'vegetation', material_key: i ? 'tree_foliage' : 'tree_bark' };
      mesh.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 0, -10));
      model.add(mesh); return mesh;
    });
    const lod = new VegetationLod('medium'); lod.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    lod.update(camera);
    const draws: THREE.InstancedMesh[] = [];
    model.traverse(o => { const m = o as THREE.InstancedMesh; if (m.isInstancedMesh && m.visible) draws.push(m); });
    expect(draws).toHaveLength(1);
    expect(draws[0].castShadow).toBe(true);
    expect(draws[0].geometry.index!.count).toBe(parts.reduce((count, mesh) => count + mesh.geometry.index!.count, 0));
    const colors = draws[0].geometry.getAttribute('color');
    expect(colors.getX(0)).toBeCloseTo(bark.color.r);
    expect(colors.getX(colors.count - 1)).toBeCloseTo(leaf.color.r);
    camera.position.set(0, 0, 300); lod.update(camera);
    const far: THREE.InstancedMesh[] = [];
    model.traverse(o => { const m = o as THREE.InstancedMesh; if (m.isInstancedMesh && m.visible) far.push(m); });
    expect(far).toHaveLength(1);
    expect((far[0].material as THREE.MeshStandardMaterial).vertexColors).toBe(false);
    expect((far[0].material as THREE.MeshStandardMaterial).color.r).toBeCloseTo(leaf.color.r);
    lod.remove(model);
    expect(model.children).toEqual(parts);
    expect(parts.every(mesh => mesh.visible)).toBe(true);
    expect(bark.color.getHex()).toBe(0x442211);
  });

  it('preserves authored draft tree colours and part batching', () => {
    const model = new THREE.Group(), scene = new THREE.Scene(); scene.add(model);
    const original = new THREE.MeshStandardMaterial({ color: 0x996633 });
    for (const key of ['tree_bark', 'tree_foliage']) {
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), original, 1);
      mesh.userData = { layer: 'vegetation', material_key: key };
      mesh.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 0, -10)); model.add(mesh);
    }
    tintDraft(model, {} as THREE.WebGPURenderer);
    const tinted = (model.children[0] as THREE.InstancedMesh).material as THREE.MeshStandardMaterial;
    expect(tinted.type).toBe('MeshStandardMaterial');
    expect(tinted.color.toArray()).toEqual(original.color.toArray());
    expect(tinted).not.toBe(original);
    expect(original.color.getHex()).toBe(0x996633);
    const lod = new VegetationLod('medium'); lod.add(model);
    lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
    let draws = 0;
    model.traverse(o => { if ((o as THREE.InstancedMesh).isInstancedMesh && o.visible) draws++; });
    expect(draws).toBe(1);
    lod.remove(model);
  });

  it('never expands vegetation instances into a static tile shadow mesh', () => {
    const { model, mesh } = trees();
    expect(batchTileShadows(model)).toBeNull();
    expect(mesh.castShadow).toBe(false);
  });
});

it('moving past equal-distance cells preserves packed matrices and object identities', () => {
  const scene = new THREE.Scene(), model = new THREE.Group();
  const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial(), 2);
  mesh.userData = { layer: 'vegetation', material_key: 'tree_foliage', instance_object_ids: ['west', 'east'] };
  mesh.setMatrixAt(0, new THREE.Matrix4().makeTranslation(-10, 0, -100));
  mesh.setMatrixAt(1, new THREE.Matrix4().makeTranslation(10, 0, -100));
  model.add(mesh); scene.add(model);
  const lod = new VegetationLod('medium'), camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
  lod.add(model); camera.position.x = -1; lod.update(camera);
  const batches: THREE.InstancedMesh[] = [];
  model.traverse(o => { if ((o as THREE.InstancedMesh).isInstancedMesh && o !== mesh) batches.push(o as THREE.InstancedMesh); });
  const versions = batches.map(batch => batch.instanceMatrix.version);
  const visible = batches.find(batch => batch.visible)!;
  const ids = visible.userData.instance_object_ids;
  expect(ids).toEqual(['west', 'east']);
  expect(visible.count).toBe(2);
  camera.position.x = 1; lod.update(camera);
  expect(batches.map(batch => batch.instanceMatrix.version)).toEqual(versions);
  expect(visible.userData.instance_object_ids).toBe(ids);
  expect(ids).toEqual(['west', 'east']);
  lod.remove(model);
});
