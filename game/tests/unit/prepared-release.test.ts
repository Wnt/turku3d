import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { expect, it } from 'vitest';
import { readPreparedScene, buildPreparedModel } from '../../src/world/preparedScene';
import { GroundIndex } from '../../src/world/groundIndex';
import { readCol } from '../../src/physics/colTile';

it('installs the shipped prepared release with matching collider identities and bounded material draws', async () => {
  const path = resolve(process.env.PREPARED_SCENE_PATH ?? 'public/scenes/office.scene.json');
  const scene = readPreparedScene(JSON.parse(readFileSync(path, 'utf8')));
  const model = buildPreparedModel(scene);
  expect(model.children.length).toBeLessThanOrEqual(scene.materials.length);
  for (const material of scene.materials) for (const texture of [material.baseColorTexture, material.normalTexture, material.ormTexture]) {
    if (!texture) continue;
    const bytes = readFileSync(resolve(dirname(path), texture.url));
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(texture.sha256);
  }
  for (const asset of scene.assets) {
    const bytes = readFileSync(resolve(dirname(path), asset.url));
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(asset.sha256);
    expect(bytes.readUInt32LE(0)).toBe(0x46546c67);
    expect(bytes.readUInt32LE(8)).toBe(bytes.length);
  }
  const ground = new GroundIndex();
  let triangles = 0;
  for (const [id, entry] of Object.entries(scene.collision)) {
    const bytes = readFileSync(resolve(dirname(path), entry.url));
    expect(bytes.length).toBe(entry.bytes);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(entry.sha256);
    const col = await readCol(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
    ground.add(col);
    expect(col.id).toBe(id); expect(col.indices.length / 3).toBe(entry.tris); triangles += entry.tris;
  }
  for (const [x, y, z] of [scene.spawn.position, scene.spawn.vehicle.position]) {
    const height = ground.ground(x, y + 1, z, 2);
    expect(height, 'compiled spawn must stand on its collision support').not.toBeNull();
    expect(Math.abs(height! - y)).toBeLessThan(.35);
  }
  expect(triangles).toBeGreaterThan(0);
  expect(scene.meshes.filter(m => m.collision === 'solid').length).toBeGreaterThan(0);
});
