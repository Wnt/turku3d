import { cachedTest as test, expect } from './fixtures';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';

test('WebGPU first order switches to mission music and advances the deadline', { tag: ['@audio', '@gameplay'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'Music state is independent of the render backend');
  await page.goto(`/?backend=${info.project.name}`);
  await page.waitForFunction(() => (window as any).__game?.ready);
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().music.state)).toBe('menu');
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().music.state)).toBe('explore');

  // Drive the real mission system through its market and pizzeria actions with synthetic positions;
  // the fixture tiles do not contain the remote shop geometry.
  await page.evaluate(({ market, pizzeria }) => {
    const missions = (window as any).__game.missions;
    const frame = (position: { x: number; z: number }, extra = {}) => ({ position, interact: false, input: 'keyboard', vehicleId: null, ...extra });
    missions.update(0, frame(market, { interact: true }));
    missions.update(0, frame(market, { choose: 'rent_permit' }));
    missions.update(0, frame(market, { leave: true }));
    missions.update(0, frame(pizzeria, { interact: true }));
    missions.update(0, frame(pizzeria, { choose: 'take_order' }));
  }, { market: MARKET.point, pizzeria: PIZZERIA.point });
  await expect.poll(() => page.evaluate(() => (window as any).__game.missions.phase)).toBe('carrying');
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().music.playing?.state), { timeout: 10_000 }).toBe('mission');
  const first = await page.evaluate(() => (window as any).__game.audio.snapshot().music.deadline);
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().music.deadline), { timeout: 10_000 }).toBeGreaterThan(first);
});
