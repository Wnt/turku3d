import fs from 'node:fs';
import path from 'node:path';
import type { CDPSession } from '@playwright/test';
import { test, expect, gameState, type Page } from './fixtures';
import { frames, untilSim } from './waits';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { PIZZERIA } from '../../src/gameplay/content/locations';
import { SLICE_PRICE, START_MONEY } from '../../src/gameplay/economy';

// Phone in landscape: touch input, mobile viewport, Chromium. The fixtures stub out host gamepads.
test.use({ hasTouch: true, isMobile: true, viewport: { width: 844, height: 390 } });
const shots = path.join(reportsRoot, 'gameplay-touch-controls');

/** Multi-touch through CDP: each call sends every finger that is down, so fingers move and lift independently. */
class Fingers {
  private points = new Map<number, { x: number; y: number }>();
  constructor(private readonly cdp: CDPSession) {}
  private send(type: 'touchStart' | 'touchMove' | 'touchEnd') {
    const touchPoints = [...this.points].map(([id, p]) => ({ id, x: p.x, y: p.y, radiusX: 6, radiusY: 6, force: 1 }));
    return this.cdp.send('Input.dispatchTouchEvent', { type, touchPoints });
  }
  async down(id: number, x: number, y: number) { this.points.set(id, { x, y }); await this.send('touchStart'); }
  async move(id: number, x: number, y: number) { this.points.set(id, { x, y }); await this.send('touchMove'); }
  /** Lifts one finger; the others stay down. */
  async up(id: number) {
    this.points.delete(id);
    await this.send(this.points.size ? 'touchMove' : 'touchEnd');
  }
  /** Moves a finger in steps, one rendered frame apart, like a thumb drag. */
  async drag(page: Page, id: number, dx: number, dy: number, steps = 8) {
    const p = this.points.get(id)!, x = p.x, y = p.y;
    for (let i = 1; i <= steps; i++) { await this.move(id, x + dx * i / steps, y + dy * i / steps); await frames(page, 1); }
  }
}

async function centre(page: Page, selector: string) {
  const box = (await page.locator(selector).boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}
const fullscreen = (page: Page) => page.evaluate(() => document.fullscreenElement !== null);
const shot = async (page: Page, backend: string, name: string) => {
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${backend}-${name}.png`) });
};

async function start(page: Page, backend: string, spawn: string) {
  await page.goto(`/?backend=${backend}&spawn=${spawn}`);
  const button = page.getByRole('option', { name: 'New game' });
  await expect(button).toBeEnabled();
  expect(await fullscreen(page), 'the start screen runs in the normal browser view').toBe(false);
  await expect(page.locator('#gamepad-status')).toHaveText('Touch controls ready.');
  await expect(page.locator('.start-touch')).toBeVisible();
  await expect(page.locator('#touch-controls')).toBeHidden();
  await shot(page, backend, 'start');
  await button.tap();
  await page.getByRole('button', { name: 'Skip' }).tap();
  await expect.poll(() => fullscreen(page), 'closing the start screen enters fullscreen').toBe(true);
  await page.waitForFunction(() => (window.__game!.state as () => { grounded: boolean })().grounded);
  await expect(page.locator('#touch-controls')).toBeVisible();
  return new Fingers(await page.context().newCDPSession(page));
}

test('touch: stick walks, look drag turns, buttons work together, fullscreen exit pauses', { tag: ['@input', '@launch', '@synthetic'] }, async ({ page }, info) => {
  const backend = info.project.name;
  const fingers = await start(page, backend, '23459668,6704254');
  const s0 = await gameState(page);
  expect(s0.inputSource).toBe('touch');
  await expect(page.locator('#help')).toBeHidden();
  const forward = await page.evaluate(() => (window.__game!.orbit as { basis(): { fx: number; fz: number } }).basis());

  // Left thumb lands and pushes up: the player walks along the camera's forward direction.
  await fingers.down(1, 160, 260);
  await fingers.drag(page, 1, 0, -70);
  await untilSim(page, ([x, z, fx, fz]) => {
    const s = (window.__game!.state as () => { feet: number[] })();
    return (s.feet[0] - x) * fx + (s.feet[2] - z) * fz > 1.2;
  }, [s0.feet[0], s0.feet[2], forward.fx, forward.fz], 20);
  await shot(page, backend, 'walk');
  const walked = await gameState(page);
  const along = (walked.feet[0] - s0.feet[0]) * forward.fx + (walked.feet[2] - s0.feet[2]) * forward.fz;
  const across = (walked.feet[0] - s0.feet[0]) * forward.fz - (walked.feet[2] - s0.feet[2]) * forward.fx;
  expect(along).toBeGreaterThan(1.2);
  expect(Math.abs(across)).toBeLessThan(along * 0.3);

  // While the stick is held, the right thumb drags left (camera turns) and a third finger taps Jump.
  await fingers.down(2, 640, 180);
  await fingers.drag(page, 2, -120, 0);
  const turned = await gameState(page);
  expect(turned.cameraYaw - s0.cameraYaw, 'a left drag turns the camera left like the mouse').toBeGreaterThan(0.2);
  const jump = await centre(page, '.touch-button[data-action="jump"]:not([hidden])');
  const ground = turned.feet[1];
  await fingers.down(3, jump.x, jump.y);
  await frames(page, 2);
  await fingers.up(3);
  await untilSim(page, y => (window.__game!.state as () => { feet: number[] })().feet[1] > y + 0.3, ground, 5);
  expect((await gameState(page)).mode).toBe('foot');
  await fingers.up(2);
  await fingers.up(1);
  await untilSim(page, () => { const s = (window.__game!.state as () => { vel: number[]; grounded: boolean })(); return s.grounded && Math.hypot(s.vel[0], s.vel[2]) < 0.05; }, undefined, 20);

  // Swiping out of fullscreen pauses behind the overlay; a tap returns to fullscreen and resumes.
  await page.evaluate(() => document.exitFullscreen());
  await expect(page.locator('#fullscreen-overlay')).toBeVisible();
  await expect(page.locator('#touch-controls')).toBeHidden();
  await expect.poll(async () => (await gameState(page)).paused).toBe(true);
  await shot(page, backend, 'fullscreen-overlay');
  await page.locator('#fullscreen-overlay button').tap();
  await expect.poll(() => fullscreen(page)).toBe(true);
  await expect(page.locator('#fullscreen-overlay')).toBeHidden();
  await expect.poll(async () => (await gameState(page)).paused).toBe(false);
});

test('touch: the Use button is the A button, shop options take a tap and Back leaves', { tag: ['@input', '@launch', '@synthetic'] }, async ({ page }, info) => {
  const backend = info.project.name;
  await start(page, backend, '23459668,6704254');
  // The pizzeria is inside the synthetic tiles. Target distance is horizontal, so any ground height there will do.
  await page.evaluate(async target => {
    const game = window.__game as any;
    const hit = await game.physics.raycast({ x: target.x + 1.5, y: 120, z: target.z }, { x: 0, y: -1, z: 0 }, 200);
    game.physics.setCharacterEnabled(true, { x: target.x + 1.5, y: (hit?.point.y ?? 2) + 0.1, z: target.z });
  }, PIZZERIA.point);
  await untilSim(page, t => {
    const s = (window.__game!.state as () => { feet: number[]; grounded: boolean })();
    return Math.hypot(s.feet[0] - t.x, s.feet[2] - t.z) < 3 && s.grounded;
  }, PIZZERIA.point, 20);
  const use = page.locator('.touch-button[data-action="mission"]:not([hidden])');
  await expect(use).toContainText('Ⓐ');
  await use.tap();
  await expect(page.locator('#shop-menu')).toBeVisible();
  await expect(page.locator('#shop-menu .shop-hints')).toContainText('Ⓑ');
  await expect(page.locator('.touch-button[data-action="menuBack"]')).toBeVisible();
  await expect(page.locator('.touch-button[data-action="jump"]')).toBeHidden();
  await shot(page, backend, 'shop');
  await page.locator('#shop-menu [data-option="slice"]').tap();
  await expect.poll(async () => ((await gameState(page)) as unknown as { missions: { money: number } }).missions.money).toBe(START_MONEY - SLICE_PRICE);
  const selected = ((await gameState(page)) as unknown as { shopMenu: { selected: number } }).shopMenu.selected;
  expect(await page.locator('#shop-menu li').nth(selected).getAttribute('data-option')).toBe('slice');
  await page.locator('.touch-button[data-action="menuBack"]').tap();
  await expect(page.locator('#shop-menu')).toBeHidden();
  await expect(page.locator('.touch-button[data-action="jump"]')).toBeVisible();
});
