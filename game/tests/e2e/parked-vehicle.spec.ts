import { isolatedTest as test, expect } from './fixtures';
import { Document, NodeIO } from '@gltf-transform/core';

test('walk to a parked car, use Xbox Y, drive 20 m, and exit', { tag: ['@drive', '@input'] }, async ({ page }, info) => {
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
  await page.goto(`/?backend=${info.project.name}`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => (window as any).__game?.state?.().grounded);
  await page.evaluate(() => (window as any).__game.physics.setCharacterEnabled(true, { x: 125, y: 2, z: 167.8 }));
  await page.waitForFunction(() => Math.abs((window as any).__game.state().feet[2] - 167.8) < 0.2);
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(150);
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
  await page.waitForFunction(z => (window as any).__game.state().vehicle.pos[2] > z + 20, start, { timeout: 60_000 });
  await button(7, 0);
  await button(6, 1);
  await page.waitForFunction(() => (window as any).__game.state().vehicle.speed < 0.1);
  await button(6, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  await button(3, 0);
  expect((await page.evaluate(() => (window as any).__game.state().vehicles.find((v: any) => v.id === 'fixture/parked').pos[2]))).toBeGreaterThan(start + 20);
});
