import { test, expect } from '@playwright/test';
import { DataUtils } from 'three/webgpu';
import fs from 'node:fs/promises';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

// A moving car's history lookup must land on its previous screen position. A clipped or
// quantised velocity leaves old body/wheel edges behind even when a still screenshot looks OK.
test('car history follows signed motion at driving speed', { tag: ['@drive', '@render'] }, async ({ page }, info) => {
  await page.setViewportSize({ width: 960, height: 540 });
  await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`/?backend=${info.project.name}&hud=0`);
  await page.waitForFunction(() => {
    const g = window.__game as any;
    return g?.ready && g.state().grounded && g.state().tilesSettled;
  });
  // Position the driver beside the parked car, then use the normal input/worker/visual path.
  await page.evaluate(() => {
    const g = window.__game as any, v = g.state().vehicle.pos;
    g.physics.spawnCharacter({ x: v[0] + 1, y: v[1], z: v[2] });
  });
  await page.waitForFunction(() => {
    const s = (window.__game as any).state();
    return Math.hypot(s.feet[0] - s.vehicle.pos[0], s.feet[2] - s.vehicle.pos[2]) < 2;
  });
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window.__game as any).state().mode === 'car');
  await page.keyboard.down('KeyW');
  await page.waitForFunction(() => (window.__game as any).state().vehicle.speed >= 15);
  await page.keyboard.up('KeyW');

  const captures = await page.evaluate(async (backend) => {
    const r = window.__render!, g = window.__game as any;
    const scene = g.tiles.tiles.group.parent, car = scene.getObjectByName('car');
    const original = r.renderer.render.bind(r.renderer);
    let target: any, position: any, focus: any;
    // Fixed roadside views isolate object motion from camera-follow latency. Both signs matter:
    // an unsigned attachment clamps leftward motion and rounds rightward motion to whole bytes.
    r.renderer.render = (s, c) => {
      if (s === scene) { c.position.copy(position); c.lookAt(focus); c.updateMatrixWorld(); }
      original(s, c);
      const rt = r.renderer.getRenderTarget();
      if (s === scene && rt?.textures.some(t => t.name === 'velocity')) target = rt;
    };
    const captures = [];
    try {
      for (const side of [-1, 1]) {
        position = car.position.clone().add({ x: side * 10, y: 7, z: 0 });
        focus = car.position.clone().add({ x: 0, y: 0.7, z: 0 });
        // Drain the view change and establish temporal history before measuring consecutive frames.
        for (let i = 0; i < 4; i++) await new Promise(requestAnimationFrame);
        const frames = [], reads: Promise<number[]>[] = [];
        for (let i = 0; i < 20; i++) {
          await new Promise(requestAnimationFrame);
          const state = g.state(), p = car.position.clone();
          p.y += 0.65; p.project(r.camera); // inside the visible roof, away from silhouette edges
          const x = (p.x + 1) * 480, y = (1 - p.y) * 270;
          const index = target.textures.findIndex((t: any) => t.name === 'velocity');
          reads.push(r.renderer.readRenderTargetPixelsAsync(target, Math.round(x),
            backend === 'webgl' ? 539 - Math.round(y) : Math.round(y), 1, 1, index).then(a => Array.from(a)));
          frames.push({ frame: r.renderer.info.frame, speed: state.vehicle.speed, x, y,
            type: target.textures[index].type, physics: state.vehicle.pos, visual: car.position.toArray(),
            image: r.renderer.domElement.toDataURL() });
        }
        const velocities = await Promise.all(reads);
        captures.push({ side, frames: frames.map((f, i) => ({ ...f, velocity: velocities[i] })) });
      }
    } finally { r.renderer.render = original; }
    return captures;
  }, info.project.name);

  const directory = path.join(reportsRoot, 'car-ghost', process.env.GHOST_CAPTURE ?? 'after', info.project.name);
  await fs.mkdir(directory, { recursive: true });
  const measurements = [];
  for (const { side, frames } of captures) {
    for (let i = 0; i < frames.length; i++) {
      const { image, ...frame } = frames[i];
      await fs.writeFile(path.join(directory, `${side}-${i}.png`), Buffer.from(image.split(',')[1], 'base64'));
      const raw = frame.velocity[0];
      const ndc = frame.type === 1016 ? DataUtils.fromHalfFloat(raw) : frame.type === 1009 ? raw / 255 : raw;
      const expected = i ? frame.x - frames[i - 1].x : null;
      measurements.push({ side, ...frame, motionPixels: ndc * 480, expected,
        historyErrorPixels: expected === null ? null : Math.abs(ndc * 480 - expected) });
    }
  }
  const report = path.join(directory, 'motion.json');
  await fs.writeFile(report, JSON.stringify(measurements, null, 2));
  await info.attach('car-motion', { path: report, contentType: 'application/json' });
  for (const { frames } of captures) for (let i = 1; i < frames.length; i++) {
    expect(frames[i].frame - frames[i - 1].frame, 'capture must not skip rendered frames').toBe(1);
    expect(frames[i].speed).toBeGreaterThanOrEqual(10);
    expect(frames[i].speed).toBeLessThanOrEqual(20);
    expect(frames[i].visual).toEqual(frames[i].physics);
  }
  for (const side of [-1, 1]) {
    const moving = measurements.filter(f => f.side === side && f.expected !== null && Math.abs(f.expected) > 2);
    expect(moving.length, 'exercise actual vehicle motion in each direction').toBeGreaterThan(8);
    expect(Math.max(...moving.map(f => f.historyErrorPixels!)), 'car history lookup must track the previous physics silhouette within 1.25 pixels').toBeLessThan(1.25);
  }
  expect(errors).toEqual([]);
});
