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
  const hud = new PositionHud(camera, tiles);
  hud.update(0, true);
  expect(hud.visible).toBe(true);
  expect(hud.element.textContent).toContain('Centre: office · Test street 1');
  hud.toggle();
  expect(new PositionHud(camera, tiles).visible).toBe(false);
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
