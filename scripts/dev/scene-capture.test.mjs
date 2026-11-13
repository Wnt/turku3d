import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { buildInProgress, cameraPose, comparable, poseCamera, readViews, sessionUrl } from './scene-capture.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));

test('every maintained view set is valid', () => {
  for (const name of fs.readdirSync(path.join(here, 'views'))) {
    const views = readViews(path.join(here, 'views', name));
    assert.ok(views.views.length >= 3, `${name}: three fixed views`);
    // Gameplay sets come with a walk; reference sets take their cameras from the photos.
    assert.ok(views.references ? views.views.every(v => v.photo) : views.walk?.seconds > 0, `${name}: a walking clip or photo cameras`);
  }
});

test('a view set without cameras is refused', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'views-')), 'bad.json');
  fs.writeFileSync(file, JSON.stringify({ schema: 'turku-capture-views/1', time: 'x', viewport: [10, 10], renderScale: 1, views: [{ id: 'a', camera: { position: [0, 0] } }] }));
  assert.throws(() => readViews(file), /needs id and camera/);
});

test('comparisons are controlled only with matched settings and cameras', () => {
  const camera = { position: [1, 2, 3], quaternion: [0, 0, 0, 1], fov: 60 };
  const capture = (settings, cam = camera) => ({ settings: { quality: 'medium', time: 't', weather: 'clear', viewport: [1440, 960], renderScale: 0.5, ...settings }, views: [{ id: 'a', requested: cam }] });
  assert.deepEqual(comparable(capture({}), capture({})), { controlled: true, reasons: [] });
  assert.deepEqual(comparable(capture({ time: 'u' }), capture({})).reasons, ['time differs']);
  assert.deepEqual(comparable(capture({}), capture({}, { ...camera, fov: 50 })).reasons, ['view a camera differs']);
});

test('session URLs: solo only where asked, scene and quality from the view set', () => {
  const url = sessionUrl('http://localhost:5690/', { quality: 'medium', scene: 'port-arthur' }, 'solo');
  assert.equal(url.searchParams.get('solo'), '1');
  assert.equal(url.searchParams.get('region'), 'port-arthur');
  assert.equal(url.searchParams.get('quality'), 'medium');
  assert.equal(sessionUrl('https://city.lab.madekivi.fi/', { scene: 'office' }, 'multiplayer').searchParams.has('solo'), false);
  // A location of the world pipeline replaces the prepared scene of the view set.
  const world = sessionUrl('http://localhost:5690/', { scene: 'port-arthur' }, 'solo', 'portsa');
  assert.equal(world.searchParams.get('world'), 'portsa');
  assert.equal(world.searchParams.has('region'), false);
});

test('a surveyed pose becomes the camera the QA mode applies (Euler YXZ, vertical fov, city origin)', async () => {
  const THREE = createRequire(path.join(here, '../../game/package.json'))('three');
  const pose = { E: 23459675.963, N: 6704242.703, H: 5.622, yaw: 324.819, pitch: 10, roll: 3, fov: 65 };
  const camera = poseCamera(pose);
  assert.deepEqual(camera.position.map(v => +v.toFixed(3)), [175.963, 5.622, 257.297]);
  const expected = new THREE.Quaternion().setFromEuler(new THREE.Euler(10 * Math.PI / 180, -324.819 * Math.PI / 180, -3 * Math.PI / 180, 'YXZ'));
  camera.quaternion.forEach((v, i) => assert.ok(Math.abs(v - expected.toArray()[i]) < 1e-12));
  const look = new THREE.Vector3(0, 0, -1).applyQuaternion(new THREE.Quaternion(...camera.quaternion));
  assert.ok(look.x < 0 && look.z < 0, 'a north-west bearing looks towards -x (west) and -z (north)');
});

test('render mode: the QA camera URL of the first view, and a world camera round-trips through a pose', () => {
  const views = readViews(path.join(here, 'views', 'riverside-office.json'));
  const url = sessionUrl('http://localhost:5690/', views, 'render');
  assert.equal(url.searchParams.get('time'), views.time);
  assert.equal(url.searchParams.has('solo') || url.searchParams.has('hud'), false);
  const cam = url.searchParams.get('cam').split(',').map(Number);
  assert.equal(cam.length, 7);
  for (const view of views.views) {
    const back = poseCamera(cameraPose(view.camera));
    back.position.forEach((v, i) => assert.ok(Math.abs(v - view.camera.position[i]) < 1e-6));
    // q and -q are the same rotation.
    const dot = back.quaternion.reduce((s, v, i) => s + v * view.camera.quaternion[i], 0);
    assert.ok(Math.abs(Math.abs(dot) - 1) < 1e-9, `${view.id}: quaternion round trip`);
  }
});

test('a location that is being built is refused; a marker of a process that is gone is ignored', () => {
  const marker = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'building-')), 'portsa.json');
  assert.equal(buildInProgress(marker), null);
  fs.writeFileSync(marker, JSON.stringify({ name: 'portsa', host: os.hostname(), pid: process.pid, started: '2026-11-13T02:00:00Z' }));
  assert.equal(buildInProgress(marker).pid, process.pid);
  fs.writeFileSync(marker, JSON.stringify({ name: 'portsa', host: os.hostname(), pid: 2 ** 22 - 1, started: '2026-11-13T02:00:00Z' }));
  assert.equal(buildInProgress(marker), null);
  fs.writeFileSync(marker, JSON.stringify({ name: 'portsa', host: 'another-host', pid: 1, started: '2026-11-13T02:00:00Z' }));
  assert.equal(buildInProgress(marker).host, 'another-host');
});
