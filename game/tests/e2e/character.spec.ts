import { expect, isolatedTest as test } from './fixtures';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { settled } from './waits';

test('textured player stays framed in third person', { tag: ['@walk'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const response = await page.request.get('/assets/character/player.glb');
  expect(response.ok()).toBe(true);
  const asset = await response.body();
  const json = JSON.parse(asset.subarray(20, 20 + asset.readUInt32LE(12)).toString());
  expect(json.extensionsRequired).toEqual(expect.arrayContaining(['EXT_meshopt_compression', 'KHR_texture_basisu']));
  expect(json.meshes.some((mesh: { name: string }) => mesh.name === 'Cloth_hoodie')).toBe(true);
  expect(json.animations.map((a: { name: string }) => a.name)).toEqual(expect.arrayContaining([
    'Idle_Loop', 'Walk_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop', 'Walk_Formal_Loop', 'Jump_Loop',
  ]));
  const height = Math.max(...json.meshes.flatMap((mesh: any) => mesh.primitives.map((p: any) => json.accessors[p.attributes.POSITION].max[1])));
  expect(height).toBeGreaterThan(1.7);
  expect(height).toBeLessThan(1.9);
  await page.goto(`/?scene=dev&backend=${info.project.name}`);
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  await settled(page);
  // Project the actual bind-pose body height from the default orbit camera using its actual pose.
  const bounds = await page.evaluate(height => {
    const s = (window as any).__game.state();
    const camera = s.camera.pos, feet = s.visualFeet;
    const target = [feet[0], feet[1] + 1.25, feet[2]];
    const f = target.map((v: number, i: number) => v - camera[i]);
    const len = Math.hypot(...f); for (let i = 0; i < 3; i++) f[i] /= len;
    const r = [-f[2], 0, f[0]], rl = Math.hypot(...r); for (let i = 0; i < 3; i++) r[i] /= rl;
    const u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]];
    const dot = (a: number[], b: number[]) => a.reduce((sum, v, i) => sum + v * b[i], 0);
    return [0, height].map(height => {
      const p = feet.map((v: number, i: number) => v - camera[i] + (i === 1 ? height : 0));
      return 0.5 - dot(p, u) / (2 * dot(p, f) * Math.tan(s.camera.fov * Math.PI / 360));
    });
  }, height);
  expect(Math.min(...bounds)).toBeGreaterThan(0.05);
  expect(Math.max(...bounds)).toBeLessThan(0.95);
  expect(Math.abs(bounds[1] - bounds[0])).toBeGreaterThan(0.2);
  await page.screenshot({ path: path.join(reportsRoot, 'screenshots', `${info.project.name}-player-framing.png`) });
  expect(errors).toEqual([]);
});
