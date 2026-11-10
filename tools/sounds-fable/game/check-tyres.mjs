// In-game check of the synthesised tyres: normal driving must stay silent, a provoked slide must squeal, and several
// squealing cars must not cost frames. BROWSER_CDP=... BASE=... node check-tyres.mjs OUT.json [new|multiplayer]
import fs from 'node:fs';
import { BASE, audioLoad, connect, drainCarEvents, fpsAB, log, openGame, recordCarEvents, sleep, summarise, takeCar } from './lib.mjs';

const [out, option = 'new'] = process.argv.slice(2);
const browser = await connect();
const room = `sf-tyres-${Date.now() % 100000}`;
const { context, page, errors } = await openGame(browser, { option, query: `&traffic=0&walkers=0${option === 'multiplayer' ? `&room=${room}` : ''}` });
const result = { base: BASE, option, problems: [] };
try {
  result.version = await page.evaluate(() => fetch('version.json').then(r => r.json()).catch(() => null));
  await recordCarEvents(page);
  const car = await takeCar(page, { place: { x: 81.04, z: 190.96 } });
  // The placement pauses the world while the tiles there load: drive only once it runs again. Whatever the tyres did
  // during the placement itself is reported apart from the driving.
  await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && s.mode === 'car' && !s.vehicle?.held; }, null, { timeout: 30000 });
  await sleep(1500);
  result.placement = summarise(await drainCarEvents(page));
  log(`took ${car.id}; placement`, JSON.stringify(result.placement));
  // A: the autopilot drives to the market and back at city speeds: no squeal expected, apart from real tight turns.
  await page.evaluate(() => { window.__trip = window.__game.autopilot.goTo({ x: 291.32, z: 210.84 }, { mode: 'drive', speed: 50 / 3.6, limitFactor: 1.2, stop: true, arrive: 12 }); window.__tripState = null; window.__trip.done.then(r => { window.__tripState = r.state; }); });
  const t0 = Date.now();
  while (Date.now() - t0 < 90000 && !(await page.evaluate(() => window.__tripState))) await sleep(1000);
  result.autopilot = { trip: await page.evaluate(() => window.__tripState), ...summarise(await drainCarEvents(page)) };
  if (result.autopilot.trip !== 'arrived') result.problems.push(`the autopilot leg ended as ${result.autopilot.trip}`);
  log('autopilot leg', JSON.stringify(result.autopilot));
  await page.evaluate(() => window.__game.autopilot.cancel('check'));
  result.voicesAfterDrive = await page.evaluate(() => window.__game.audio.snapshot().tyres);
  // B: a provoked slide: accelerate, then full lock with the handbrake.
  await page.keyboard.down('KeyW'); await sleep(3500);
  await page.keyboard.down('KeyD'); await page.keyboard.down('Space'); await sleep(2500);
  result.duringSlide = await page.evaluate(() => window.__game.audio.snapshot().tyres);
  await page.keyboard.up('Space'); await page.keyboard.up('KeyD'); await page.keyboard.up('KeyW');
  await sleep(1500);
  result.slide = summarise(await drainCarEvents(page));
  log('provoked slide', JSON.stringify(result.slide), JSON.stringify(result.duringSlide));
  // C: frame rate and audio-thread load with several cars squealing: three synthetic remote cars slide hard around
  // the player while the local car's voice squeals too, interleaved with quiet stretches.
  const squealOn = () => page.evaluate(() => {
    const g = window.__game, frame = { slide: 7, load: 1.4, speed: 14, squeal: 1, rough: 0.35, grit: 0 };
    clearInterval(window.__squeal);
    window.__squeal = setInterval(() => { const [x, y, z] = g.state().vehicle?.pos ?? g.state().feet;
      for (let i = 0; i < 3; i++) g.audio.tyreVoice(`remote:check${i}`, { kind: 'car', local: false, position: { x: x + 8 + 6 * i, y, z: z + 5 - 5 * i }, frame: { ...frame, slide: 6 + i } });
      g.audio.tyreVoice('local', { kind: 'car', local: true, position: { x, y, z }, frame }); }, 16);
  });
  const squealOff = () => page.evaluate(() => { clearInterval(window.__squeal); for (let i = 0; i < 3; i++) window.__game.audio.tyreVoice(`remote:check${i}`, null); });
  const quietLoad = await audioLoad(page, 5);
  await squealOn(); await sleep(1500);
  result.squealingVoices = await page.evaluate(() => window.__game.audio.snapshot().tyres);
  const loadedLoad = await audioLoad(page, 5);
  result.audioThread = { quiet: quietLoad, fourSquealing: loadedLoad };
  result.fps = await fpsAB(page, squealOn, squealOff, { rounds: Number(process.env.ROUNDS ?? 4), seconds: 8 });
  await sleep(700);
  result.voicesReleased = await page.evaluate(() => window.__game.audio.snapshot().tyres.voices.map(v => v.id));
  log('fps', JSON.stringify(result.fps), 'audio thread', JSON.stringify(result.audioThread));
  result.audio = await page.evaluate(() => { const s = window.__game.audio.snapshot(); return { state: s.state, loaded: s.loaded, loops: s.loopSounds, errors: s.errors, engines: s.engines.voices.length }; });
  // Verdicts.
  const p = result.problems;
  if (!result.voicesAfterDrive.ready || !result.voicesAfterDrive.voices.some(v => v.local)) p.push('no local tyre voice after driving');
  if (result.autopilot.slidingShare > 0.05) p.push(`normal driving slides ${(100 * result.autopilot.slidingShare).toFixed(1)} % of frames`);
  if (result.slide.maxSlide < 2) p.push(`the provoked slide reached only ${result.slide.maxSlide} m/s`);
  if (result.squealingVoices.voices.length < 4) p.push(`only ${result.squealingVoices.voices.length} tyre voices with three remote cars`);
  if (result.fps.change < -3) p.push(`fps changed ${result.fps.change} % with squealing cars (${result.fps.offMean} -> ${result.fps.onMean})`);
  if (result.voicesReleased.some(id => id.startsWith('remote:'))) p.push('remote voices were not released');
  if (result.audio.errors.length) p.push('audio errors: ' + result.audio.errors.join('; '));
  if (errors.length) p.push('page errors: ' + errors.slice(0, 3).join(' | '));
} catch (e) { result.problems.push('check failed: ' + e.message.split('\n')[0]); }
if (out) fs.writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result.problems.length ? 'PROBLEMS\n  ' + result.problems.join('\n  ') : 'OK');
await context.close();
process.exit(result.problems.length ? 1 : 0);
