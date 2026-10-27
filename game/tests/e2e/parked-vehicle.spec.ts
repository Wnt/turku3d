import { isolatedTest as test, expect } from './fixtures';
import { open, untilSim } from './waits';
import { Document, NodeIO } from '@gltf-transform/core';

test('taken parked car stays visible after driving away on both backends', { tag: ['@drive', '@input'] }, async ({ page }, info) => {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const positions = new Float32Array([
    -0.9, 0, -2.2, 0.9, 0, -2.2, 0.9, 1.4, -2.2, -0.9, 1.4, -2.2,
    -0.9, 0, 2.2, 0.9, 0, 2.2, 0.9, 1.4, 2.2, -0.9, 1.4, 2.2,
  ]);
  const indices = new Uint16Array([
    0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
    0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2,
    1, 2, 6, 1, 6, 5, 0, 4, 7, 0, 7, 3,
  ]);
  const primitive = doc.createPrimitive()
    .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(positions).setBuffer(buffer))
    .setIndices(doc.createAccessor().setType('SCALAR').setArray(indices).setBuffer(buffer))
    .setMaterial(doc.createMaterial().setBaseColorFactor([0.25, 0.4, 0.7, 1]));
  doc.createScene().addChild(doc.createNode('parked-car').setMesh(doc.createMesh().addPrimitive(primitive)));
  const glb = Buffer.from(await new NodeIO().writeBinary(doc));
  await page.route('**/dev-tiles/manifest.json', async route => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.vehicles = [{ object_id: 'fixture/parked', model: 'fixture', color: 'blue',
      x: 125, y: 2, z: 165, yaw: 0, width: 1.8, height: 1.4, length: 4.4 }];
    manifest.vehicle_models = { 'fixture/blue': { url: 'parked-test.glb', sha256: '', bytes: glb.length } };
    await route.fulfill({ response, json: manifest });
  });
  await page.route('**/dev-tiles/parked-test.glb', route => route.fulfill({ body: glb, contentType: 'model/gltf-binary' }));
  await page.addInitScript(() => {
    const w = window as any;
    w.pad = { id: 'Xbox Wireless Controller (virtual)', index: 0, connected: true, mapping: 'standard',
      axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })), timestamp: 0 };
    Object.defineProperty(navigator, 'getGamepads', { value: () => [w.pad] });
  });
  const button = (index: number, value: number) => page.evaluate(([i, v]) => {
    (window as any).pad.buttons[i] = { value: v, pressed: v > 0.5, touched: v > 0 };
  }, [index, value]);
  await open(page, `/?backend=${info.project.name}`);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await page.waitForFunction(() => (window as any).__game?.state?.().grounded);
  await page.evaluate(() => (window as any).__game.physics.setCharacterEnabled(true, { x: 125, y: 2, z: 167.8 }));
  await page.waitForFunction(() => Math.abs((window as any).__game.state().feet[2] - 167.8) < 0.2);
  await page.keyboard.down('KeyW');
  await page.waitForFunction(() => (window as any).__game.state().feet[2] < 167.6);
  await page.keyboard.up('KeyW');
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return Math.hypot(s.feet[0] - 125, s.feet[2] - 165) < 4;
  });
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().drivenId === 'fixture/parked');
  await button(3, 0);
  const start = await page.evaluate(() => (window as any).__game.state().vehicle.pos[2]);
  await button(7, 1);
  await untilSim(page, z => (window as any).__game.state().vehicle.pos[2] > z + 60, start, 60);
  await button(7, 0);
  const visual = await page.evaluate(() => {
    const game = (window as any).__game;
    const camera = (window as any).__render.camera;
    const origin = new camera.position.constructor(125, 2, 165).project(camera);
    return { car: game.parkedVisuals.inspect('fixture/parked', camera), originalProjection: [origin.x, origin.y, origin.z] };
  });
  expect(visual.car).toMatchObject({ parented: true, visible: true, castShadow: true, staticInstances: 0 });
  expect(Math.abs(visual.car.projected[0])).toBeLessThan(1);
  expect(Math.abs(visual.car.projected[1])).toBeLessThan(1);
  expect(visual.car.projected[2]).toBeGreaterThan(0);
  expect(visual.car.projected[2]).toBeLessThan(1);
  expect(visual.originalProjection[2] < 0 || visual.originalProjection[2] > 1
    || Math.abs(visual.originalProjection[0]) > 1 || Math.abs(visual.originalProjection[1]) > 1).toBe(true);
  await button(6, 1);
  await page.waitForFunction(() => (window as any).__game.state().vehicle.speed < 0.5);
  await button(6, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  await button(3, 0);
  const afterExit = await page.evaluate(() => {
    const game = (window as any).__game;
    return { physicsZ: game.state().vehicles.find((v: any) => v.id === 'fixture/parked').pos[2],
      visual: game.parkedVisuals.inspect('fixture/parked', (window as any).__render.camera) };
  });
  expect(afterExit.physicsZ).toBeGreaterThan(start + 60);
  expect(afterExit.visual).toMatchObject({ parented: true, staticInstances: 0 });
  expect(Math.abs(afterExit.visual.position[2] - afterExit.physicsZ)).toBeLessThan(0.5);
});
