// Four real headed clients against the deployed game and room. No accelerated server clock.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeExecutable, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(reportsRoot, 'timed-rounds'); fs.mkdirSync(out, { recursive: true });
const contexts = [], pages = [], evidence = [], errors = [];
const url = 'https://wnt.github.io/turku-game/game/';
const expected = process.argv[2] ?? fs.readFileSync(path.join(workRoot, 'build/game/index.html'), 'utf8').match(/assets\/main-[\w-]+\.js/)[0];
const started = Date.now();
const deadline = setTimeout(() => { console.error('Ten-minute round probe deadline'); process.exit(1); }, 590000);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const state = page => page.evaluate(() => window.__game.multiplayerClient().state.rounds);
const option = (page, id) => page.locator(`#pause-menu [data-option="${id}"]`);
async function shot(stage) {
  for (const [i, page] of pages.entries()) await page.screenshot({ path: path.join(out, `${stage}-player-${i + 1}.png`), timeout: 10000 });
  try { execFileSync('/usr/sbin/screencapture', ['-x', '-m', path.join(out, `${stage}-display.png`)], { timeout: 5000 }); }
  catch (error) { console.log(`Display capture unavailable: ${error.message}`); }
  evidence.push({ stage, at: Date.now(), rounds: await state(pages[0]) });
  fs.writeFileSync(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ stage, elapsed: (Date.now() - started) / 1000 }));
}
async function pause(page) {
  if (!await page.locator('#pause-menu').isVisible()) {
    await page.keyboard.press('Escape'); await page.locator('#pause-menu').waitFor({ state: 'visible' });
  }
}
async function rounds(page) { await pause(page); await option(page, 'timed-rounds').click(); }
async function pad(page, button) {
  await page.evaluate(i => { window.__roundPad.buttons[i] = 1; }, button); await wait(160);
  await page.evaluate(i => { window.__roundPad.buttons[i] = 0; }, button); await wait(160);
}
async function voteAndStart(stage) {
  await rounds(pages[0]); await option(pages[0], 'round-propose').click();
  await pages[1].waitForFunction(() => !!window.__game.multiplayerClient().state.rounds.proposal);
  await shot(`${stage}-vote-banner`);
  await rounds(pages[1]);
  // The first enabled row is Agree. Exercise the real gamepad A binding.
  await pad(pages[1], 0);
  const proposal = (await state(pages[0])).proposal;
  for (let i = 2; i < proposal.needed; i++) {
    assert.ok(pages[i], 'test players must be able to reach half the connected room');
    await rounds(pages[i]); await option(pages[i], 'round-agree').tap();
  }
  await pages[0].waitForFunction(() => {
    const p = window.__game.multiplayerClient().state.rounds.proposal;
    return p && p.votes.length >= p.needed;
  });
  await option(pages[0], 'round-start').click();
  await Promise.all(pages.map(p => p.locator('#round-countdown').waitFor({ state: 'visible', timeout: 10000 })));
  await shot(`${stage}-countdown`);
  await Promise.all(pages.map(p => p.waitForFunction(() => {
    const g = window.__game; return g.multiplayerClient().state.rounds.active?.phase === 'running' && !g.state().loading;
  }, null, { timeout: 30000 })));
  const ids = await Promise.all(pages.map(async p => (await state(p)).active.id));
  assert.equal(new Set(ids).size, 1);
  for (const page of pages) {
    const s = await page.evaluate(() => ({ game: window.__game.state(), spawn: window.__game.multiplayerClient().state.you.spawn }));
    assert.equal(s.game.mode, 'foot'); assert.equal(s.game.missions.money, 120);
    assert.ok(Math.hypot(s.game.feet[0] - s.spawn.E, s.game.feet[2] + s.spawn.N) < 2, 'server spawn applied');
    assert.ok(s.game.multiplayer.scores.every(s => s.earned === 0 && s.deliveries === 0));
  }
  return ids[0];
}
try {
  let served;
  do {
    const response = await fetch(`${url}?round-release=${Date.now()}`, { signal: AbortSignal.timeout(5000), cache: 'no-store' });
    served = (await response.text()).match(/assets\/main-[\w-]+\.js/)?.[0];
    if (served === expected) break;
    if (Date.now() - started > 120000) throw new Error('Pages did not propagate the uploaded bundle');
    await wait(3000);
  } while (served !== expected);
  for (let i = 0; i < 4; i++) {
    const profile = path.join(process.env.TURKU3D_BROWSER_DIR, `round-player-${i}-${Date.now()}`);
    const context = await chromium.launchPersistentContext(profile, {
      executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: false, viewport: null,
      hasTouch: true,
      args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--autoplay-policy=no-user-gesture-required']),
    });
    contexts.push(context);
    await context.addInitScript(() => {
      // Emulate touch-capable standalone windows so taps keep the four-window layout.
      Object.defineProperty(navigator, 'standalone', { value: true });
      window.__roundPad = { buttons: Array(17).fill(0) };
      Object.defineProperty(navigator, 'getGamepads', { value: () => [{ id: 'Xbox Wireless Controller', index: 0, mapping: 'standard', connected: true,
        timestamp: performance.now(), axes: [0, 0, 0, 0], buttons: window.__roundPad.buttons.map(value => ({ value, pressed: value > .5, touched: value > 0 })) }] });
    });
    const page = context.pages()[0]; pages.push(page); page.setDefaultTimeout(15000);
    page.on('pageerror', e => errors.push({ player: i + 1, message: e.message }));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    const size = await page.evaluate(() => ({ width: screen.availWidth, height: screen.availHeight, left: screen.availLeft, top: screen.availTop }));
    const { windowId } = await cdp.send('Browser.getWindowForTarget');
    await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal', left: size.left + (i % 2) * Math.floor(size.width / 2),
      top: size.top + Math.floor(i / 2) * Math.floor(size.height / 2), width: Math.floor(size.width / 2), height: Math.floor(size.height / 2) } });
    await page.goto(`${url}?quality=low&backend=webgpu&round-probe=${Date.now()}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  }
  await Promise.all(pages.map(async page => {
    await page.waitForFunction(() => window.__game?.ready, null, { timeout: 120000 });
    assert.ok((await page.content()).includes(expected));
    await page.locator('[data-option="multiplayer"]').click();
    await page.waitForFunction(() => !window.__game.state().loading && !window.__game.state().startScreen, null, { timeout: 45000 });
  }));
  await shot('01-connected');
  await pages[0].evaluate(() => window.__game.missions.grantDevMoney(100));
  const first = await voteAndStart('02-first');
  await pages[0].keyboard.down('KeyW'); await wait(1500); await pages[0].keyboard.up('KeyW');
  await shot('03-playing');
  const second = await voteAndStart('04-restart'); assert.notEqual(second, first);
  assert.equal((await state(pages[0])).history[0].reason, 'restarted');
  // Reload one client to exercise a real late/rejoining welcome during the running timer.
  await pages[3].reload({ waitUntil: 'domcontentloaded' });
  await pages[3].waitForFunction(() => window.__game?.ready, null, { timeout: 90000 });
  await pages[3].locator('[data-option="multiplayer"]').click();
  await pages[3].waitForFunction(() => !window.__game.state().loading && !window.__game.state().startScreen);
  assert.equal((await state(pages[3])).active.id, second);
  // A bounded simulated delivery exercises the live server's validation and frozen ranking.
  await pages[0].evaluate(() => window.__game.multiplayerClient().sendDelivery({ orderId: `round-probe-${Date.now()}`, addressId: 'round-probe', payout: 6, tip: 8, routeMeters: 120 }));
  await pages[1].waitForFunction(() => window.__game.multiplayerClient().state.scores.some(s => s.earned === 14));
  await shot('05-rejoin-and-score');
  while ((await state(pages[0])).active) {
    console.log(JSON.stringify({ stage: 'running', remaining: (await state(pages[0])).active?.end - Date.now() }));
    await wait(15000);
  }
  await Promise.all(pages.map(p => p.locator('[data-page="round-result"]').waitFor({ state: 'visible' })));
  await shot('06-scoreboard');
  for (const page of pages) {
    const r = (await state(page)).history[0]; assert.equal(r.id, second); assert.equal(r.reason, 'time'); assert.equal(r.scores[0].earned, 14);
    await option(page, 'dismiss').click(); await rounds(page); await option(page, 'round-history').tap();
    await option(page, second).tap();
  }
  await shot('07-past-results');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, seconds: (Date.now() - started) / 1000, report: out }));
} finally {
  clearTimeout(deadline);
  fs.writeFileSync(path.join(out, 'errors.json'), JSON.stringify(errors, null, 2));
  await Promise.all(contexts.map(c => c.close().catch(() => {})));
}
