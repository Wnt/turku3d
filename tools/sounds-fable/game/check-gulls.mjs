// In-game check of the gull calls: by the river a call comes now and then as a placed one-shot, inland never, in the
// harbour again; the two files are fetched only after the game is playable. Reads audio.snapshot().gulls.
//   BROWSER_CDP=... BASE=... node check-gulls.mjs OUT.json [new|multiplayer]
import fs from 'node:fs';
import { BASE, connect, log, openGame, sleep, takeCar } from './lib.mjs';

const [out, option = 'new'] = process.argv.slice(2);
const browser = await connect();
const { context, page, errors, atReady, audioRequests, marks } = await openGame(browser, { option, query: `&traffic=0&walkers=0${option === 'multiplayer' ? `&room=sf-gulls-${Date.now() % 100000}` : ''}` });
const result = { base: BASE, option, problems: [] };
const gulls = () => page.evaluate(() => { const s = window.__game.audio.snapshot(); return { ...s.gulls, loops: s.loopSounds, voices: s.voices, errors: s.errors }; });
const waitFor = async (test, seconds) => { const end = Date.now() + seconds * 1000; let g; do { g = await gulls(); if (test(g)) return g; await sleep(500); } while (Date.now() < end); return g; };
try {
  result.version = await page.evaluate(() => fetch('version.json').then(r => r.json()).catch(() => null));
  result.atReady = { gullsLoaded: atReady.gulls?.loaded, loaded: atReady.loaded };
  // 1: the spawn is 60 m from the Aura's centreline: the files load now, and a first call comes within 10-30 s.
  const first = await waitFor(g => g.calls >= 1, 50);
  result.river = first;
  result.marks = marks;
  result.fetches = audioRequests.filter(r => /gull-/.test(r.file));
  result.audioBeforePlayable = audioRequests.filter(r => r.s <= marks.playable).length;
  log('river', JSON.stringify(first), 'fetched', JSON.stringify(result.fetches));
  // 2: four blocks inland: no presence, nothing scheduled, no call in 45 s.
  const car = await takeCar(page);
  const inland = { x: 168 - 330, z: 246 - 330 };
  await page.evaluate(p => window.__game.relocateVehicle(p.x, p.z, 0), inland); await sleep(5000);
  const before = (await gulls()).calls;
  await sleep(45000);
  result.inland = await gulls();
  result.inland.newCalls = result.inland.calls - before;
  log('inland', JSON.stringify(result.inland));
  // 3: the harbour, on the quay by the ferry terminals: a call within 10-30 s, placed over the basin.
  // (The player may have left the car meanwhile: a relocation moves the car, so get back in first.)
  if (await page.evaluate(() => window.__game.state().mode) !== 'car') await takeCar(page);
  result.harbourPlaced = await page.evaluate(() => window.__game.relocateVehicle(-2364, 1560, 0)); await sleep(6000);
  result.harbourAt = await page.evaluate(() => window.__game.state().camera.pos.map(Math.round));
  const callsBefore = (await gulls()).calls;
  result.harbour = await waitFor(g => g.calls > callsBefore, 60);
  log('harbour', JSON.stringify(result.harbour));
  // 4: it is a one-shot: the voice ends by itself (the longer call is 11.7 s at 0.95 x).
  result.afterCall = await waitFor(g => g.playing === null, 16);
  const p = result.problems;
  if (result.atReady.gullsLoaded) p.push('gull files were loaded before the menu');
  if (first.calls < 1) p.push('no call by the river within 50 s');
  if (first.last && first.last.where !== 'river') p.push(`the river call was placed in the ${first.last.where}`);
  if (result.fetches.length !== 2) p.push(`${result.fetches.length} gull fetches`);
  if (result.fetches.some(f => f.s < marks.playable)) p.push('a gull file was fetched before playable');
  if (result.inland.presence !== 0 || result.inland.newCalls) p.push(`inland: presence ${result.inland.presence}, ${result.inland.newCalls} calls`);
  if (result.harbour.calls <= callsBefore) p.push('no call in the harbour within 60 s');
  else if (result.harbour.last.where !== 'harbour') p.push(`the harbour call was placed at the ${result.harbour.last.where}`);
  if (result.afterCall.playing !== null) p.push('the call did not end');
  if (first.loops.includes('gulls')) p.push('the gull loop is still there');
  if (result.harbour.errors.length) p.push('audio errors: ' + result.harbour.errors.join('; '));
  if (errors.length) p.push('page errors: ' + errors.slice(0, 3).join(' | '));
} catch (e) { result.problems.push('check failed: ' + e.message.split('\n')[0]); }
if (out) fs.writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result.problems.length ? 'PROBLEMS\n  ' + result.problems.join('\n  ') : 'OK');
await context.close();
process.exit(result.problems.length ? 1 : 0);
