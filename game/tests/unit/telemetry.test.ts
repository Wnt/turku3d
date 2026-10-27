import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { Telemetry, telemetryEnabled, type Sample } from '../../src/telemetry';
import { extractWalls, measureWall } from '../../src/telemetry/walls';
import { tilesAhead, visualTileAt } from '../../src/telemetry/grid';

const loc = (url: string) => { const u = new URL(url); return { hostname: u.hostname, search: u.search }; };

test('telemetry runs on local origins only and never on GitHub Pages', () => {
  expect(telemetryEnabled(loc('http://localhost:5170/'), false)).toBe(true);
  expect(telemetryEnabled(loc('http://127.0.0.1:5221/?backend=webgl'), false)).toBe(true);
  expect(telemetryEnabled(loc('https://wnt.github.io/turku-game/game/'), false)).toBe(false);
  expect(telemetryEnabled(loc('https://wnt.github.io/turku-game/game/?telemetry=1'), false)).toBe(false);
  expect(telemetryEnabled(loc('https://example.org/'), false)).toBe(false);
  expect(telemetryEnabled(loc('https://example.org/?telemetry=1'), false)).toBe(true);
  // Browser test suites do not write play sessions unless they ask for it.
  expect(telemetryEnabled(loc('http://localhost:5171/'), true)).toBe(false);
  expect(telemetryEnabled(loc('http://localhost:5171/?telemetry=1'), true)).toBe(true);
});

test('level-0 tile ids match the pipeline grid, including negative indices', () => {
  expect(visualTileAt(10, 10)).toBe('t0_0_0');
  expect(visualTileAt(-0.1, 260)).toBe('t0_-1_1');
  expect(tilesAhead(240, 10, 1, 0)).toEqual(['t0_1_0', 't0_2_0']);
  expect(tilesAhead(10, 10, 0, -1)).toEqual(['t0_0_-1', 't0_0_-2']);
  expect(tilesAhead(10, 10, 0, 0)).toEqual([]);
});

/** A batched tile mesh: wall A (10 x 10 m in the plane z = 0, facing +z) and wall B (facing -x at x = 20). */
function tileModel() {
  const quad = (a: number[], b: number[], c: number[], d: number[]) => [...a, ...b, ...c, ...a, ...c, ...d];
  const positions = [
    ...quad([-5, 0, 0], [5, 0, 0], [5, 10, 0], [-5, 10, 0]),
    ...quad([20, 0, 5], [20, 0, -5], [20, 6, -5], [20, 6, 5]),
  ];
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex([...Array(12).keys()]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.userData = { layer: 'buildings', objects: [
    { start: 0, count: 6, building_id: 'LR15', facade_index: 0, normal: [0, 0, 1] },
    { start: 6, count: 6, building_id: 'LR15', facade_index: 7, normal: [-1, 0, 0] },
    { start: 0, count: 6, object_id: 'roof' },
  ] };
  const model = new THREE.Group();
  model.add(mesh);
  return model;
}

function cameraAt(x: number, y: number, z: number, target: THREE.Vector3, fov = 90) {
  const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 6000);
  camera.position.set(x, y, z);
  camera.lookAt(target);
  camera.updateMatrixWorld();
  return camera;
}

test('walls are fitted from their index ranges and measured on screen', () => {
  const walls = extractWalls(tileModel());
  expect(walls.map(w => w.id)).toEqual(['LR15_0', 'LR15_7']);
  const [a, b] = walls;
  expect(a.width).toBeCloseTo(10); expect(a.y0).toBeCloseTo(0); expect(a.y1).toBeCloseTo(10);
  expect([a.nx, a.nz]).toEqual([0, 1]);
  expect(b.width).toBeCloseTo(10); expect(b.y1).toBeCloseTo(6);

  // 90 deg vertical fov at 5 m: the 10 x 10 m wall fills the square viewport exactly.
  const full = measureWall(a, cameraAt(0, 5, 5, new THREE.Vector3(0, 5, 0)), 150)!;
  expect(full.screen_fraction).toBeCloseTo(1, 4);
  expect(full.distance_m).toBeCloseTo(5); expect(full.angle_deg).toBeCloseTo(0);
  // Half of the viewport when the camera aims at the wall's right edge.
  const half = measureWall(a, cameraAt(5, 5, 5, new THREE.Vector3(5, 5, 0)), 150)!;
  expect(half.screen_fraction).toBeCloseTo(0.5, 4);
  // From 20 m the wall spans a quarter of the view height and width.
  expect(measureWall(a, cameraAt(0, 5, 20, new THREE.Vector3(0, 5, 0)), 150)!.screen_fraction).toBeCloseTo(1 / 16, 4);
  // Oblique view: angle from the normal.
  expect(measureWall(a, cameraAt(10, 5, 10, new THREE.Vector3(0, 5, 0)), 150)!.angle_deg).toBeCloseTo(45, 0);
  // Behind the wall, out of range, behind the camera or crossing the near plane.
  expect(measureWall(a, cameraAt(0, 5, -5, new THREE.Vector3(0, 5, 0)), 150)).toBeNull();
  expect(measureWall(a, cameraAt(0, 5, 200, new THREE.Vector3(0, 5, 0)), 150)).toBeNull();
  expect(measureWall(a, cameraAt(0, 5, 5, new THREE.Vector3(0, 5, 10)), 150)).toBeNull();
  const straddle = measureWall(a, cameraAt(0, 5, 5, new THREE.Vector3(-5, 5, 0)), 150)!;
  expect(straddle.screen_fraction).toBeGreaterThan(0.1);
  expect(straddle.screen_fraction).toBeLessThan(1);
  // Wall B faces -x.
  expect(measureWall(b, cameraAt(10, 3, 0, new THREE.Vector3(20, 3, 0)), 150)!.distance_m).toBeCloseTo(10);
});

test('samples once a second, batches lines every five seconds and names missing tiles', async () => {
  const bodies: string[] = [];
  let clock = 1_800_000_000_000;
  const telemetry = new Telemetry((body) => { bodies.push(body); return true; }, () => clock);
  const model = tileModel();
  const parent = new THREE.Group();
  parent.add(model);
  telemetry.addModel(model, 't0_0_0');
  telemetry.addModel(new THREE.Group(), 't1_0_0'); // coarse parents carry no walls
  telemetry.setBuiltTiles(['t0_0_0', 't1_0_0']);
  const camera = cameraAt(0, 5, 20, new THREE.Vector3(0, 5, 0), 60);
  const pose = { x: 0, y: 0, z: 22, vehicle: null, speed: 1.5 };
  for (let ms = 0; ms <= 5100; ms += 16) { clock += 16; telemetry.tick(ms, camera, () => pose); }
  await Promise.resolve();
  expect(telemetry.stats.samples).toBe(6);
  expect(telemetry.stats.walls).toBe(2);
  expect(bodies).toHaveLength(1);
  const lines = bodies[0].trimEnd().split('\n').map(l => JSON.parse(l) as Sample);
  expect(lines).toHaveLength(6);
  const s = lines[0];
  expect(s).toMatchObject({ session: telemetry.session, E: 23459500, N: 6704478, H: 0, yaw: 0, pitch: 0, vehicle: null,
    speed: 1.5, tile: 't0_0_0' });
  expect(s.facades.map(f => f.facade_id)).toEqual(['LR15_0']);
  expect(s.facades[0].distance_m).toBe(20);
  expect(s.outside_tiles).toBeUndefined();

  // Walking east out of the built tile: the tile stood in and the two ahead.
  camera.position.set(300, 5, 22); camera.lookAt(400, 5, 22); camera.updateMatrixWorld();
  const outside = telemetry.sample(camera, { ...pose, x: 300 });
  expect(outside.yaw).toBe(90);
  expect(outside.outside_tiles).toEqual({ stand: 't0_1_0', ahead: ['t0_2_0', 't0_3_0'] });
  telemetry.removeModel(model);
  expect(telemetry.stats.walls).toBe(0);
});
