import { cachedTest as test, expect } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { frames, open, untilSim } from '../e2e/waits';

const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));
const out = path.join(reportsRoot, 'gameplay-map-route');
fs.mkdirSync(out, { recursive: true });

test('minimap routes along published streets to an injected mission target', { tag: ['@gameplay'] }, async ({ page }, info) => {
  test.skip(!manifest.roads, 'No published road graph (synthetic tiles)');
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const backend = info.project.name;
  await open(page, `/?backend=${backend}&tiles=/tiles/tileset.json`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await untilSim(page, () => {
    const s = (window as any).__game?.state?.();
    return s?.grounded && s.tilesSettled && s.visTris > 0;
  }, null, 90);
  // A street node about 300 m from the player along the walkable network becomes the active pickup.
  const setup = await page.evaluate(() => {
    const game = (window as any).__game;
    const [x, , z] = game.state().feet;
    const { router } = game;
    const nodes: Float64Array = router.graph.nodes;
    for (let i = 0; i < nodes.length / 2; i++) {
      const point = { x: nodes[2 * i], z: nodes[2 * i + 1] };
      const path = router.route({ x, z }, point);
      const length = path ? router.distance(path) : 0;
      if (length > 280 && length < 320 && Math.hypot(point.x - x, point.z - z) > 150) {
        const targets = [
          { id: 'pickup', point, label: 'Pizzeria', kind: 'pickup' },
          { id: 'dropoff', point: { x: x - 120, z: z + 60 }, label: 'Customer', kind: 'dropoff' },
          { id: 'shop', point: { x: x + 40, z: z - 30 }, label: 'Dealer', kind: 'shop' },
        ];
        const state = { targets, activeTargetId: 'pickup', money: 1250, timerSeconds: 95, message: 'Pick up the pizza from the pizzeria' };
        game.minimap.setSource({ subscribe: (listener: (s: unknown) => void) => { listener(state); return () => {}; } });
        return { start: { x, z }, target: point, streets: router.graph.edges.length };
      }
    }
    return null;
  });
  expect(setup, 'a street node 280-320 m away along the network').not.toBeNull();
  await frames(page, 3);
  const snap = await page.evaluate(() => (window as any).__game.minimap.snapshot());
  expect(snap.route.length).toBeGreaterThan(2);
  const end = snap.route[snap.route.length - 1];
  expect(Math.hypot(end.x - setup!.target.x, end.z - setup!.target.z)).toBeLessThan(0.5);
  expect(Math.hypot(snap.route[0].x - setup!.start.x, snap.route[0].z - setup!.start.z)).toBeLessThan(80);
  expect(snap.routeLength).toBeGreaterThan(280);
  expect(snap.routeLength).toBeLessThan(320);
  await expect(page.locator('#minimap .minimap-message')).toHaveText('Pick up the pizza from the pizzeria');
  await expect(page.locator('#minimap .minimap-timer')).toHaveText('1:35');
  await expect(page.locator('#minimap .minimap-money')).toHaveText('€1,250');
  // Walking forward turns the player along the camera, so the centre arrow points up the map.
  await page.keyboard.down('KeyW');
  await page.waitForFunction(() => Math.hypot(...(window as any).__game.state().vel.filter((_: number, i: number) => i !== 1)) > 2);
  await frames(page, 30);
  await page.keyboard.up('KeyW');
  const view = (await page.evaluate(() => (window as any).__game.minimap.snapshot())).view;
  expect(Math.abs(((view.facingDeg - view.headingDeg + 540) % 360) - 180)).toBeLessThan(25);
  await page.locator('#minimap').screenshot({ path: path.join(out, `minimap-${backend}.png`) });
  await page.screenshot({ path: path.join(out, `game-${backend}.png`) });

  // Moving more than 5 m re-plans from the new position.
  const moved = { x: snap.route[2].x, z: snap.route[2].z };
  await page.evaluate(p => (window as any).__game.physics.setCharacterEnabled(true, { x: p.x, y: (window as any).__game.state().feet[1] + 1, z: p.z }), moved);
  await page.waitForFunction(p => {
    const route = (window as any).__game.minimap.snapshot().route;
    return route && Math.hypot(route[0].x - p.x, route[0].z - p.z) < 10;
  }, moved);

  await page.keyboard.press('KeyM');
  await page.waitForFunction(() => (window as any).__game.minimap.snapshot().cssSize > 240);
  await frames(page, 2);
  await page.screenshot({ path: path.join(out, `map-expanded-${backend}.png`) });
  await page.keyboard.press('KeyM');
  await page.waitForFunction(() => (window as any).__game.minimap.snapshot().cssSize === 240);
  const final = await page.evaluate(() => (window as any).__game.minimap.snapshot());
  fs.writeFileSync(path.join(out, `minimap-${backend}.json`), JSON.stringify({
    streets: setup!.streets, routePoints: snap.route.length, routeLength: snap.routeLength,
    routeComputeMs: final.routeComputeMs, drawMs: final.drawMs, draws: final.draws }, null, 1));
  expect(final.drawMs, 'minimap draw time per redraw (ms)').toBeLessThan(4);
  expect(errors).toEqual([]);
});
