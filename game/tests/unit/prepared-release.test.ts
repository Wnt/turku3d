import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { expect, it } from 'vitest';
import { readPreparedScene, buildPreparedModel } from '../../src/world/preparedScene';
import { readCol } from '../../src/physics/colTile';

it('installs the shipped prepared release with matching collider identities and bounded material draws', async () => {
  const path = resolve(process.env.PREPARED_SCENE_PATH ?? 'public/scenes/office.scene.json');
  const scene = readPreparedScene(JSON.parse(readFileSync(path, 'utf8')));
  const model = buildPreparedModel(scene);
  expect(model.children.length).toBeLessThanOrEqual(scene.materials.length);
  let triangles = 0;
  for (const [id, entry] of Object.entries(scene.collision)) {
    const bytes = readFileSync(resolve(dirname(path), entry.url));
    expect(bytes.length).toBe(entry.bytes);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(entry.sha256);
    const col = await readCol(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
    expect(col.id).toBe(id); expect(col.indices.length / 3).toBe(entry.tris); triangles += entry.tris;
  }
  expect(triangles).toBeGreaterThan(0);
  expect(scene.meshes.filter(m => m.collision === 'solid').length).toBeGreaterThan(0);
});
