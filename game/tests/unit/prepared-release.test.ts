import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, dirname, basename } from 'node:path';
import { createHash } from 'node:crypto';
import { expect, it } from 'vitest';
import { readPreparedScene, buildPreparedModel } from '../../src/world/preparedScene';
import { GroundIndex } from '../../src/world/groundIndex';
import { readCol } from '../../src/physics/colTile';
import { prepareLighting } from '../../src/render/lights/preparedLighting';
import { poolLamp } from '../../src/render/lights/pools';

/** Generated shared assets are immutable host-store products, outside the source tree. */
function releaseFile(scenePath: string, url: string) {
  const local = resolve(dirname(scenePath), url);
  if (existsSync(local)) return local;
  if (!/^shared\/[a-f0-9]{64}\.(?:glb|ktx2|png|jpg|webp)$/.test(url)) return local;
  return resolve(process.env.TURKU3D_PREPARED_ASSETS ?? `${homedir()}/turku3d/data/prepared-assets`, url);
}

it('installs the shipped prepared release with matching collider identities and bounded material draws', async () => {
  const path = resolve(process.env.PREPARED_SCENE_PATH ?? 'public/scenes/office.scene.json');
  const scene = readPreparedScene(JSON.parse(readFileSync(path, 'utf8')));
  const model = buildPreparedModel(scene);
  expect(model.children.length).toBeLessThanOrEqual(scene.materials.length * 2);
  for (const material of scene.materials) for (const texture of [material.baseColorTexture, material.normalTexture, material.ormTexture]) {
    if (!texture) continue;
    const bytes = readFileSync(releaseFile(path, texture.url));
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(texture.sha256);
  }
  const checkedDependencies = new Set<string>();
  for (const asset of scene.assets) {
    const assetPath = releaseFile(path, asset.url);
    const bytes = readFileSync(assetPath);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(asset.sha256);
    expect(bytes.readUInt32LE(0)).toBe(0x46546c67);
    expect(bytes.readUInt32LE(8)).toBe(bytes.length);
    // A valid main GLB does not prove its external images were packaged. Missing
    // KTX2 files previously passed this check and left an imported facade white.
    expect(bytes.readUInt32LE(16)).toBe(0x4e4f534a);
    const document = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
    for (const image of document.images ?? []) {
      if (!image.uri) continue;
      const dependency = resolve(dirname(assetPath), image.uri);
      if (checkedDependencies.has(dependency)) continue;
      checkedDependencies.add(dependency);
      const imageBytes = readFileSync(dependency);
      expect(createHash('sha256').update(imageBytes).digest('hex')).toBe(basename(dependency).split('.')[0]);
    }
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
  const lighting = prepareLighting(scene);
  expect(lighting.lamps).toHaveLength(scene.emitters.length);
  for (const emitter of scene.emitters) {
    const lamp = lighting.lamps.find(l => l.key === emitter.id)!;
    const fixture = scene.fixtures.find(f => f.id === emitter.fixtureId)!;
    expect(lamp.post).toBe(false);
    expect(lamp.photometry).toEqual({ color: emitter.color, intensity: emitter.intensity, range: emitter.range, base: fixture.position[1],
      ...(emitter.aperture ? { aperture: emitter.aperture } : {}) });
    if (emitter.aperture) {
      const pool = poolLamp(lamp)!;
      expect([pool.x,pool.y,pool.z]).toEqual(emitter.aperture.position);
      expect([lamp.light.x,lamp.light.y,lamp.light.z]).toEqual(emitter.position);
    }
    if (emitter.intensity > 0) expect(poolLamp(lamp)).toMatchObject({ intensity: emitter.intensity, range: emitter.range,
      r: emitter.color[0], g: emitter.color[1], b: emitter.color[2] });
    const [x, y, z] = fixture.position, support = ground.ground(x, y + .15, z, .3);
    expect(support, `mapped emitter fixture ${fixture.id} needs physical support`).not.toBeNull();
    expect(Math.abs(support! - y)).toBeLessThan(.1);
  }
  for (const draw of model.children as import('three/webgpu').Mesh[]) {
    const night = draw.geometry.getAttribute('_night_irradiance');
    if (!night) continue;
    expect(draw.geometry.userData.preparedStreetLighting).toBe(true);
    const parts = scene.meshes.filter(m => m.render !== false && m.nightIrradiance !== undefined
      && m.materialId === (draw.material as import('three/webgpu').Material).name);
    expect(Array.from(night.array)).toEqual(Array.from(new Float32Array(parts.flatMap(m => m.nightIrradiance!))));
  }
  expect(triangles).toBeGreaterThan(0);
  expect(scene.meshes.filter(m => m.collision === 'solid').length).toBeGreaterThan(0);
});
