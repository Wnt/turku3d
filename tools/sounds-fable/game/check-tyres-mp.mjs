// Multiplayer check of the tyre voices: two real clients in one room (two windows of one Chrome). The driver slides
// near the listener; the listener must get a tyre voice for the driver's car from the synced wheels.
//   BROWSER_CDP=... BASE=https://city.madekivi.fi/ node check-tyres-mp.mjs OUT.json
import fs from 'node:fs';
import { BASE, connect, log, openGame, sleep, takeCar } from './lib.mjs';

const [out] = process.argv.slice(2);
const browser = await connect();
const room = `sf-tyres-${Date.now() % 100000}`, query = `&traffic=0&walkers=0&room=${room}`;
const result = { base: BASE, room, problems: [] };
const a = await openGame(browser, { option: 'multiplayer', query, label: 'driver' });
const b = await openGame(browser, { option: 'multiplayer', query, label: 'listener' });
try {
  for (const g of [a, b]) await g.page.waitForFunction(() => window.__game.state().multiplayer?.connected, null, { timeout: 60000 });
  // The driver on the road at the pizzeria stop; the listener parks 14 m away.
  await takeCar(a.page, { place: { x: 81.04, z: 190.96 } });
  await takeCar(b.page, { place: { x: 95, z: 190.96 } });
  await a.page.bringToFront();
  result.players = await b.page.evaluate(() => window.__game.state().multiplayer.players);
  // Circles with the handbrake coming on and off: slides and rolling for 14 s.
  const samples = [];
  const sampler = setInterval(async () => { try { samples.push(await b.page.evaluate(() => { const s = window.__game.audio.snapshot(); return { voices: s.tyres.voices, candidates: s.tyres.candidates }; })); } catch { /* closing */ } }, 200);
  await a.page.keyboard.down('KeyW'); await sleep(2500); await a.page.keyboard.down('KeyD');
  for (let i = 0; i < 4; i++) { await a.page.keyboard.down('Space'); await sleep(1200); await a.page.keyboard.up('Space'); await sleep(1600); }
  await a.page.keyboard.up('KeyD'); await a.page.keyboard.up('KeyW');
  await sleep(2500);
  clearInterval(sampler);
  const remote = samples.flatMap(s => s.voices.filter(v => !v.local));
  result.listener = { samples: samples.length, withRemoteVoice: samples.filter(s => s.voices.some(v => !v.local)).length,
    remoteIds: [...new Set(remote.map(v => v.id))], maxRemoteSlide: Math.max(0, ...remote.map(v => v.slide)), maxRemoteSpeed: Math.max(0, ...remote.map(v => v.speed)),
    slidingSamples: remote.filter(v => v.slide > 0.4).length, maxCandidates: Math.max(0, ...samples.map(s => s.candidates)) };
  result.driver = await a.page.evaluate(() => window.__game.audio.snapshot().tyres);
  await sleep(2500);
  result.afterStop = await b.page.evaluate(() => window.__game.audio.snapshot().tyres.voices.map(v => v.id));
  result.audioErrors = [...await a.page.evaluate(() => window.__game.audio.snapshot().errors), ...await b.page.evaluate(() => window.__game.audio.snapshot().errors)];
  log('listener', JSON.stringify(result.listener), 'after stop', JSON.stringify(result.afterStop));
  const p = result.problems;
  if (!result.listener.withRemoteVoice) p.push('the listener never had a tyre voice for the other player');
  if (result.listener.maxRemoteSlide < 1) p.push(`the remote voice never slid (max ${result.listener.maxRemoteSlide} m/s)`);
  if (result.audioErrors.length) p.push('audio errors: ' + result.audioErrors.join('; '));
  if (a.errors.length || b.errors.length) p.push('page errors: ' + [...a.errors, ...b.errors].slice(0, 3).join(' | '));
} catch (e) { result.problems.push('check failed: ' + e.message.split('\n')[0]); }
if (out) fs.writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result.problems.length ? 'PROBLEMS\n  ' + result.problems.join('\n  ') : 'OK');
await a.context.close(); await b.context.close();
process.exit(result.problems.length ? 1 : 0);
