import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { enToLatLon } from '../../src/core/geographic';
import { PositionHud, positionReadout } from '../../src/ui/positionHud';
import { VisualTiles } from '../../src/world/tiles';

// Independently projected with pyproj EPSG:3877 -> EPSG:4326, always_xy=True.
test.each([
  [23459668, 6704254, 60.44838073770597, 22.267256272543907],
  [23450000, 6695000, 60.364254436886306, 22.0939449594896],
  [23470000, 6715000, 60.545721124808416, 22.453333131565834],
])('projects E %s N %s in Google Maps coordinate order', (E, N, lat, lon) => {
  const result = enToLatLon(E, N);
  expect(result.lat).toBeCloseTo(lat, 6);
  expect(result.lon).toBeCloseTo(lon, 6);
});

test('formats camera pose, signed pitch, heading wrap and negative visual tile', () => {
  const north = new THREE.Vector3(Math.sin(0.64 * Math.PI / 180), 0, -Math.cos(0.64 * Math.PI / 180));
  const lines = positionReadout(new THREE.Vector3(168, 12, 246), north);
  expect(lines[0]).toBe('Camera 60.448381, 22.267256');
  expect(lines[1]).toContain('E 23459668.00 N 6704254.00 · H 12.00 m N2000');
  expect(lines[2]).toBe('0° N · pitch 0.0° · t0_0_0');
  expect(positionReadout(new THREE.Vector3(-1, 0, -251), new THREE.Vector3(1, -1, 0))[2])
    .toBe('89° E · pitch -45.0° · t0_-1_-2');
});

afterEach(() => vi.unstubAllGlobals());

test('defaults on, persists toggle, copies current pose and picks the nearest visible surface', async () => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (k: string) => values.get(k), setItem: (k: string, v: string) => values.set(k, v) });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append: () => {} } });
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  const camera = new THREE.PerspectiveCamera();
  const tiles = new VisualTiles();
  const building = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), new THREE.MeshBasicMaterial());
  building.position.z = -10;
  building.userData = { building_id: 'office', address: 'Test street 1' };
  building.updateMatrixWorld();
  tiles.models.add(building);
  const hud = new PositionHud(camera, tiles, true);
  hud.update(0, true);
  expect(hud.visible).toBe(true);
  expect(hud.element.textContent).toContain('Centre: office · Test street 1');
  hud.toggle();
  expect(new PositionHud(camera, tiles, true).visible).toBe(false);
  camera.position.x = 168;
  await hud.copy();
  expect(writeText.mock.calls[0][0]).toContain('E 23459668.00');
  hud.toggle();
  const ground = new THREE.Mesh(new THREE.BoxGeometry(20, 20, 1), new THREE.MeshBasicMaterial());
  ground.position.z = -5; ground.updateMatrixWorld(); tiles.models.add(ground);
  camera.position.x = 0;
  hud.update(1000, true);
  expect(hud.element.textContent).not.toContain('Centre:');
  expect(hud.element.textContent).toContain('Position copied');
  hud.update(1600, false);
  expect(hud.element.hidden).toBe(true);
});

test('resolves a hit index to its source building and preserves instance addresses', async () => {
  const { buildingTarget } = await import('../../src/ui/positionHud');
  const object = new THREE.Mesh();
  object.userData = { layer: 'buildings', object_id: 't0_2_1/batch/28/4', objects: [
    { start: 0, count: 6, building_id: 'first' },
    { start: 6, count: 12, building_id: '103440141R', address: 'Known address' },
  ] };
  const hit: THREE.Intersection = { object, faceIndex: 2, distance: 1, point: new THREE.Vector3() };
  expect(buildingTarget(hit)).toBe('103440141R · Known address');
  hit.faceIndex = 1;
  expect(buildingTarget(hit)).toBe('first');
  hit.faceIndex = 6;
  expect(buildingTarget(hit)).toBe('');
  object.userData = { layer: 'buildings', instance_objects: [
    { building_id: 'first' }, { building_id: 'second', address: 'Instance address' },
  ] };
  hit.instanceId = 1;
  expect(buildingTarget(hit)).toBe('second · Instance address');
});

test('nearest bounds avoid raycasting distant meshes; hidden and throttled readouts do no queries', () => {
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append() {} } });
  const tiles = new VisualTiles(), camera = new THREE.PerspectiveCamera();
  const far = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  far.position.z = -100; far.userData.building_id = 'far'; far.updateMatrixWorld();
  const near = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  near.position.z = -10; near.userData.building_id = 'near'; near.updateMatrixWorld();
  tiles.models.add(far); tiles.models.add(near);
  const farQuery = vi.spyOn(far, 'raycast'), nearQuery = vi.spyOn(near, 'raycast');
  const hud = new PositionHud(camera, tiles, true);
  hud.update(0, true);
  expect(hud.element.textContent).toContain('Centre: near');
  expect(farQuery).not.toHaveBeenCalled();
  expect(nearQuery).toHaveBeenCalledOnce();
  hud.update(200, true); hud.update(600, false);
  hud.visible = false; hud.update(1200, true);
  expect(nearQuery).toHaveBeenCalledOnce();
  expect((hud as any).hits).toHaveLength(0);
  expect((hud as any).candidates).toHaveLength(0);
});

test('stationary targets reuse queries but motion, streaming, filters, freshness and copy invalidate them', async () => {
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append() {} } });
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  const tiles = new VisualTiles(), camera = new THREE.PerspectiveCamera();
  const building = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), new THREE.MeshBasicMaterial());
  building.position.z = -10; building.userData.building_id = 'office'; building.updateMatrixWorld();
  tiles.models.add(building);
  const query = vi.spyOn(building, 'traverseVisible');
  let visibility = 0;
  const hud = new PositionHud(camera, tiles, true, () => visibility);
  for (const now of [0, 500, 1000, 1500]) hud.update(now, true);
  expect(query).toHaveBeenCalledOnce();
  expect(hud.element.textContent).toContain('Centre: office');
  hud.update(2000, true); expect(query).toHaveBeenCalledTimes(2);
  camera.position.x = .1;
  hud.update(2500, true); expect(query).toHaveBeenCalledTimes(3);
  camera.rotation.y = .005;
  hud.update(3000, true); expect(query).toHaveBeenCalledTimes(4);
  // A replacement can keep the same resident model count.
  tiles.loadedModels++;
  hud.update(3500, true); expect(query).toHaveBeenCalledTimes(5);
  tiles.models.add(new THREE.Group());
  hud.update(4000, true); expect(query).toHaveBeenCalledTimes(6);
  visibility++;
  hud.update(4500, true); expect(query).toHaveBeenCalledTimes(7);
  building.visible = false;
  hud.update(5000, true); expect(query).toHaveBeenCalledTimes(7);
  hud.update(6500, true); expect(query).toHaveBeenCalledTimes(8);
  expect(hud.element.textContent).not.toContain('Centre:');
  building.visible = true;
  await hud.copy();
  expect(query).toHaveBeenCalledTimes(9);
  expect(writeText.mock.calls[0][0]).toContain('Centre: office');
});

test.each(['matrix', 'count'])('instance %s changes refresh query bounds without tightening render culling', change => {
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append() {} } });
  const camera = new THREE.PerspectiveCamera(), tiles = new VisualTiles();
  const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial(), 2);
  const transform = new THREE.Matrix4();
  mesh.setMatrixAt(0, transform.makeTranslation(100, 0, -10));
  mesh.setMatrixAt(1, transform.makeTranslation(0, 0, -10));
  mesh.instanceMatrix.needsUpdate = true; mesh.count = 1;
  mesh.userData.instance_objects = [{ building_id: 'close' }, { building_id: 'close' }];
  mesh.computeBoundingBox(); mesh.computeBoundingSphere(); mesh.updateMatrixWorld();
  const renderSphere = mesh.boundingSphere!, originalSphere = renderSphere.clone();
  const computeBox = vi.spyOn(mesh, 'computeBoundingBox');
  tiles.models.add(mesh);
  const hud = new PositionHud(camera, tiles, true);
  hud.update(0, true);
  expect(hud.element.textContent).not.toContain('Centre:');
  if (change === 'matrix') {
    mesh.setMatrixAt(0, transform.makeTranslation(0, 0, -10)); mesh.instanceMatrix.needsUpdate = true;
  } else mesh.count = 2;
  camera.position.x = .01;
  hud.update(500, true);
  expect(hud.element.textContent).toContain('Centre: close');
  expect(computeBox).toHaveBeenCalledTimes(2);
  camera.position.x = .02; hud.update(1000, true);
  expect(computeBox).toHaveBeenCalledTimes(2);
  expect(mesh.boundingSphere).toBe(renderSphere);
  expect(mesh.boundingSphere).toEqual(originalSphere);
});

test('public sessions cannot show, toggle, copy or hide the character with photo shortcuts', async () => {
  vi.stubGlobal('document', { createElement: () => ({ hidden: false }), body: { append: () => {} } });
  const hud = new PositionHud(new THREE.PerspectiveCamera(), new VisualTiles(), false);
  const consume = vi.fn(() => true);
  hud.handle({ consume } as never, true);
  hud.toggle();
  await hud.copy();
  hud.update(1000, true);
  expect(consume).not.toHaveBeenCalled();
  expect(hud.visible).toBe(false);
  expect(hud.element.hidden).toBe(true);
  expect(hud.characterHidden).toBe(false);
});
