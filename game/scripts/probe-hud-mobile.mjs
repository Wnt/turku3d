// Selective deployed HUD/touch evidence; run with host_slots.py browser.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';
import { createServer } from '../../server/http.js';
import { ScoreStore } from '../../server/store.js';

isolateBrowser();
const url = 'https://wnt.github.io/turku-game/game/';
const out = path.join(reportsRoot, 'hud-mobile');
fs.mkdirSync(out, { recursive: true });
const deadline = Date.now() + 120_000;
for (;;) {
  const html = await fetch(`${url}?hud-publication=${Date.now()}`, { signal: AbortSignal.timeout(5000) }).then(r => r.text());
  if (html.includes('A reserved top row')) break;
  assert(Date.now() < deadline, 'Pages publication timed out');
  await new Promise(resolve => setTimeout(resolve, 5000));
}
const room = createServer({ store: new ScoreStore(path.join(out, 'scores')) });
await new Promise(resolve => room.server.listen(port(4), '127.0.0.1', resolve));
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, 'hud-mobile'), {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  hasTouch: true, viewport: { width: 390, height: 844 },
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
});
const hardStop = setTimeout(() => { void context.close(); }, 400_000);
try {
  const page = await context.newPage();
  await page.addInitScript(() => {
    localStorage.clear();
    Object.defineProperty(document, 'fullscreenEnabled', { get: () => false });
    Object.defineProperty(navigator, 'getGamepads', { value: () => [] });
  });
  await page.goto(`${url}?mp=${encodeURIComponent(`ws://127.0.0.1:${port(4)}/`)}`);
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 190_000 });
  await page.getByRole('option', { name: 'New game', exact: true }).tap();
  await page.getByRole('button', { name: 'Skip', exact: true }).tap();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
  await page.evaluate(() => {
    const client = window.__game.multiplayerClient();
    const scores = Array.from({ length: 6 }, (_, i) => ({ nick: `Player${i}`, rank: i + 1, deliveries: i, online: true,
      ...(i === 0 ? { earned: 42 } : i === 1 ? { money: 99 } : {}) }));
    client.receive(JSON.stringify({ v: 2, type: 'scores', scores }));
  });
  assert.equal(await page.locator('#mp-scoreboard th').nth(2).textContent(), 'Earned');
  assert.deepEqual(await page.locator('#mp-scoreboard tbody tr td:nth-child(3)').allTextContents(), ['€42', '€0', '€0']);
  assert.equal(await page.locator('#mp-scoreboard caption').count(), 0);
  assert(!(await page.locator('#city-clock').textContent()).includes('Helsinki'));
  for (const key of ['p', 'v', 'l']) await page.keyboard.press(key);
  assert(await page.locator('#position-hud').isHidden());
  for (const [width, height] of [[390, 844], [844, 390], [320, 568]]) {
    await page.setViewportSize({ width, height });
    // Keyboard shortcuts switch the active source; restore the real touch overlay.
    await page.evaluate(() => { window.__game.input.source = 'touch'; });
    await page.waitForFunction(() => !document.querySelector('#touch-controls').hidden);
    const board = await page.locator('#mp-scoreboard').boundingBox();
    for (const action of ['map', 'pause']) {
      const button = await page.locator(`.touch-corner [data-action="${action}"]:not([hidden])`).boundingBox();
      assert(button.y + button.height <= board.y, `${width}×${height}: ${action} stays above scores`);
      assert(button.x >= 0 && button.x + button.width <= width);
    }
    assert(board.x >= 0 && board.x + board.width <= width);
    await page.screenshot({ path: path.join(out, `${width}x${height}.png`) });
  }
  // Actual DOM pointer path: fixed square base and full independent axes while looking.
  const input = await page.evaluate(() => {
    const game = window.__game, canvas = document.querySelector('canvas');
    const send = (type, id, x, y) => canvas.dispatchEvent(new PointerEvent(type, { pointerType: 'touch', pointerId: id, clientX: x, clientY: y, bubbles: true }));
    send('pointerdown', 31, 80, 300); send('pointermove', 31, 280, 100);
    send('pointerdown', 32, 280, 250); send('pointermove', 32, 240, 270);
    game.input.update(1 / 60);
    const result = { steer: game.input.actions.steer, throttle: game.input.actions.throttle,
      x: game.input.lookX, y: game.input.lookY, base: document.querySelector('.touch-stick').style.transform,
      knob: document.querySelector('.touch-knob').style.transform };
    send('pointerup', 31, 280, 100); send('pointerup', 32, 240, 270);
    return result;
  });
  assert.equal(input.steer, 1); assert.equal(input.throttle, 1);
  assert(Math.abs(input.x + 0.2) < 1e-6); assert(Math.abs(input.y - 0.05) < 1e-6);
  assert.equal(input.base, 'translate(80px, 300px)'); assert.equal(input.knob, 'translate(64px, -64px)');
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({ ok: true, input }, null, 2));
  console.log('PASS: deployed mixed-version earnings, HUD cleanup, developer gating, portrait/landscape layout and square touch input (2× horizontal foot look)');
} finally {
  clearTimeout(hardStop);
  await context.close();
  await new Promise(resolve => room.server.close(resolve));
}
