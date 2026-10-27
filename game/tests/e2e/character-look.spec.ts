import { expect, isolatedTest as test } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { frames } from './waits';

test('player idle, walk and run remain grounded on both backends', { tag: ['@walk'] }, async ({ page }, info) => {
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.addInitScript(() => {
    const pad = { axes: [0, 0, 0, 0], buttons: Array(17).fill(0) };
    (window as any).__lookPad = pad;
    Object.defineProperty(navigator, 'getGamepads', { value: () => [{
      id: 'Xbox Wireless Controller', index: 0, mapping: 'standard', connected: true, timestamp: performance.now(),
      axes: pad.axes, buttons: pad.buttons.map(value => ({ value, pressed: value > 0.5, touched: value > 0 })),
    }] });
  });
  await page.goto(`/?backend=${info.project.name}`);
  await page.waitForFunction(() => (window as any).__game?.ready && (window as any).__game.state().grounded);
  await page.getByRole('option', { name: 'New game' }).click();
  const capture = process.env.PLAYER_CAPTURE;
  const directory = path.join(reportsRoot, 'player-look', capture ?? 'check');
  if (capture) fs.mkdirSync(directory, { recursive: true });
  for (const [pose, axis, sprint, minSpeed] of [
    ['idle', 0, 0, 0], ['walk', -0.45, 0, 0.5], ['run', -1, 1, 4],
  ] as const) {
    await page.evaluate(([move, button]) => {
      (window as any).__lookPad.axes[1] = move;
      (window as any).__lookPad.buttons[0] = button;
    }, [axis, sprint]);
    await page.waitForFunction(speed => {
      const state = (window as any).__game.state();
      return state.grounded && Math.hypot(state.vel[0], state.vel[2]) >= speed;
    }, minSpeed);
    await frames(page, 10);
    const state = await page.evaluate(() => (window as any).__game.state());
    expect(state.errors).toEqual([]);
    expect(Math.abs(state.visualFeet[1] - state.feet[1])).toBeLessThan(0.1);
    if (capture) await page.screenshot({ path: path.join(directory, `${info.project.name}-${pose}.png`) });
  }
});
