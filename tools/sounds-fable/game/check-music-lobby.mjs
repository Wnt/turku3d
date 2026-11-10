// The start menu's theme: a player who stays in the menu with audio on (a key press) hears it after four seconds;
// starting the game fades it out and free play follows. BROWSER_CDP=... BASE=... node check-music-lobby.mjs OUT.json
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { BASE, connect, log, sleep } from './lib.mjs';

const [out] = process.argv.slice(2);
const browser = await connect();
const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await context.newPage();
const requests = [], t0 = Date.now(), result = { base: BASE, problems: [] };
page.on('request', r => { if (/music\//.test(r.url())) requests.push({ file: r.url().split('/assets/').pop(), s: +((Date.now() - t0) / 1000).toFixed(2) }); });
const music = () => page.evaluate(() => { const s = window.__game.audio.snapshot(); return { context: s.state, ...s.music, track: s.music.playing?.track?.id ?? null }; });
try {
  await page.goto(`${BASE}?quality=low&telemetry=0&traffic=0&walkers=0&room=sf-lobby-${Date.now() % 100000}`, { waitUntil: 'domcontentloaded', timeout: 180000 });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 400000 });
  result.readyS = +((Date.now() - t0) / 1000).toFixed(2);
  await sleep(3000);
  result.beforeGesture = { ...(await music()), requests: requests.length };
  // A key that only moves the menu selection: the first gesture unlocks audio.
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowUp');
  result.unlockedS = +((Date.now() - t0) / 1000).toFixed(2);
  await sleep(2500);
  result.after2s = { ...(await music()), requests: requests.length };
  await sleep(4000);
  result.after6s = { ...(await music()), requests: [...requests] };
  log('lobby', JSON.stringify({ before: result.beforeGesture.track, after2s: result.after2s.track, after6s: result.after6s.track, requests }));
  // Start the game: the theme fades, free play follows five seconds in.
  await page.evaluate(() => document.querySelector('[data-option="multiplayer"]')?.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })));
  await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && !s.startScreen; }, null, { timeout: 120000 });
  result.enteredS = +((Date.now() - t0) / 1000).toFixed(2);
  await sleep(3500);
  result.entered3s = await music();
  await sleep(14500);
  result.entered18s = await music();
  result.requests = requests;
  log('entered', JSON.stringify({ at3s: [result.entered3s.track, result.entered3s.playing?.playing], at18s: result.entered18s.track, requests }));
  const p = result.problems;
  if (result.beforeGesture.requests) p.push('music requested before any gesture');
  if (result.after2s.requests) p.push('music requested within 2.5 s of the gesture in the start menu');
  if (result.after6s.track !== 'menu' || !result.after6s.playing?.playing) p.push(`no menu theme after 6.5 s in the start menu (${JSON.stringify(result.after6s.playing)})`);
  if (result.entered3s.playing?.playing && result.entered3s.track === 'menu') p.push('the menu theme still plays 3.5 s into the game');
  if (!['synthwave', 'lofi', 'funk', 'dubtechno'].includes(result.entered18s.track)) p.push(`no free-play track 18 s into the game (${result.entered18s.track})`);
  const early = requests.filter(r => !/menu/.test(r.file) && r.s < result.enteredS + 13);
  if (early.length) p.push(`a free-play stream opened ${(early[0].s - result.enteredS).toFixed(1)} s after entering`);
} catch (e) { result.problems.push('check failed: ' + e.message.split('\n')[0]); }
if (out) fs.writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result.problems.length ? 'PROBLEMS\n  ' + result.problems.join('\n  ') : 'OK');
await context.close();
process.exit(result.problems.length ? 1 : 0);
