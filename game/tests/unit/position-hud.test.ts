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

test('defaults on, persists toggle, copies current pose; look-at naming is off by default and does no work', async () => {
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
  const probe = vi.fn(async () => ({ toi: 9 })), query = vi.spyOn(building, 'raycast'), walk = vi.spyOn(building, 'traverseVisible');
  const hud = new PositionHud(camera, tiles, true, () => 0, probe);
  hud.update(0, true);
  expect(hud.visible).toBe(true);
  expect(hud.element.textContent).toContain('Camera');
  expect(hud.element.textContent).not.toContain('Centre:');
  for (const now of [600, 1200]) { camera.position.x += 1; hud.update(now, true); }
  await hud.copy();
  expect(probe).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled(); expect(walk).not.toHaveBeenCalled();
  expect(writeText.mock.calls[0][0]).not.toContain('Centre:');
  // The developer menu turns it on.
  camera.position.x = 0;
  hud.setNaming(true);
  hud.update(2000, true); await (hud as any).probing; hud.update(2600, true);
  expect(hud.element.textContent).toContain('Centre: office · Test street 1');
  hud.toggle();
  expect(new PositionHud(camera, tiles, true).visible).toBe(false);
  camera.position.x = 168;
  await hud.copy();
  expect(writeText.mock.calls[1][0]).toContain('E 23459668.00');
  hud.toggle();
  camera.position.x = 0;
  const later = performance.now() + 1500;
  hud.update(later, true);
  expect(hud.element.textContent).toContain('Position copied');
  hud.update(later + 600, false);
  expect(hud.element.hidden).toBe(true);
  hud.setNaming(false); hud.update(later + 1200, true);
  expect(hud.element.textContent).not.toContain('Centre:');
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

test('with collision, only building meshes at the collision hit are raycast; ground meshes never are', async () => {
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append() {} } });
  const tiles = new VisualTiles(), camera = new THREE.PerspectiveCamera();
  const building = new THREE.Mesh(new THREE.BoxGeometry(4, 4, 4), new THREE.MeshBasicMaterial());
  building.position.z = -10; building.userData.building_id = 'office'; building.updateMatrixWorld();
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(250, 250, 50, 50), new THREE.MeshBasicMaterial());
  ground.userData.layer = 'ground'; ground.updateMatrixWorld();
  const behind = new THREE.Mesh(new THREE.BoxGeometry(4, 4, 4), new THREE.MeshBasicMaterial());
  behind.position.z = -40; behind.userData.building_id = 'behind'; behind.updateMatrixWorld();
  for (const m of [building, ground, behind]) tiles.models.add(m);
  const groundQuery = vi.spyOn(ground, 'raycast'), buildingQuery = vi.spyOn(building, 'raycast'), behindQuery = vi.spyOn(behind, 'raycast');
  let toi: number | null = 8;
  const probe = vi.fn(async () => toi === null ? null : { toi });
  const hud = new PositionHud(camera, tiles, true, () => 0, probe);
  hud.setNaming(true);
  hud.update(0, true); await (hud as any).probing;
  hud.update(500, true);
  expect(hud.element.textContent).toContain('Centre: office');
  expect(buildingQuery).toHaveBeenCalledOnce();
  expect(behindQuery).not.toHaveBeenCalled();
  // A nearer surface on collision (a parked van, a sign) hides the building.
  toi = 3; camera.position.x = .01;
  hud.update(1000, true); await (hud as any).probing; hud.update(1500, true);
  expect(hud.element.textContent).not.toContain('Centre:');
  // Nothing on collision: nothing is named and no render mesh is raycast.
  toi = null; camera.position.x = 2;
  for (const now of [2000, 2500, 3000]) { hud.update(now, true); await (hud as any).probing; }
  expect(groundQuery).not.toHaveBeenCalled();
  expect(buildingQuery).toHaveBeenCalledOnce();
  expect(probe).toHaveBeenCalledTimes(3); // a still view does not probe again
  hud.update(5000, true); await (hud as any).probing;
  expect(probe).toHaveBeenCalledTimes(3);
});

test('merged building meshes: only the ranges whose bounds hold the collision hit are tested, never the whole mesh', async () => {
  vi.stubGlobal('localStorage', { getItem: () => null });
  vi.stubGlobal('document', { createElement: () => ({ hidden: false, textContent: '' }), body: { append() {} } });
  const tiles = new VisualTiles(), camera = new THREE.PerspectiveCamera();
  // Two buildings in one merged mesh: index ranges 0-35 (x = 0, near) and 36-71 (x = 30).
  const a = new THREE.BoxGeometry(4, 4, 4).translate(0, 0, -10), b = new THREE.BoxGeometry(4, 4, 4).translate(30, 0, -10);
  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.Float32BufferAttribute([...a.attributes.position.array, ...b.attributes.position.array], 3));
  merged.setIndex([...a.index!.array, ...Array.from(b.index!.array, i => i + a.attributes.position.count)]);
  const mesh = new THREE.Mesh(merged, new THREE.MeshBasicMaterial());
  mesh.userData = { layer: 'buildings', objects: [{ start: 0, count: 36, building_id: 'near' }, { start: 36, count: 36, building_id: 'far' }] };
  mesh.updateMatrixWorld(); tiles.models.add(mesh);
  const whole = vi.spyOn(mesh, 'raycast');
  const hud = new PositionHud(camera, tiles, true, () => 0, async () => ({ toi: 8 }));
  hud.setNaming(true);
  hud.update(0, true); await (hud as any).probing; hud.update(500, true);
  expect(hud.element.textContent).toContain('Centre: near');
  expect(whole).not.toHaveBeenCalled();
  camera.position.x = 30; camera.updateMatrixWorld();
  hud.update(1000, true); await (hud as any).probing; hud.update(1500, true);
  expect(hud.element.textContent).toContain('Centre: far');
});

test.each(['matrix', 'count'])('instance %s changes refresh query bounds without tightening render culling', async change => {
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
  const hud = new PositionHud(camera, tiles, true, () => 0, async () => ({ toi: 9.5 }));
  hud.setNaming(true);
  hud.update(0, true); await (hud as any).probing;
  hud.update(1000, true);
  expect(hud.element.textContent).not.toContain('Centre:');
  if (change === 'matrix') {
    mesh.setMatrixAt(0, transform.makeTranslation(0, 0, -10)); mesh.instanceMatrix.needsUpdate = true;
  } else mesh.count = 2;
  camera.position.x = .01;
  hud.update(2000, true); await (hud as any).probing; hud.update(2500, true);
  expect(hud.element.textContent).toContain('Centre: close');
  expect(computeBox).toHaveBeenCalledTimes(2);
  camera.position.x = .02; hud.update(4000, true); await (hud as any).probing;
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
