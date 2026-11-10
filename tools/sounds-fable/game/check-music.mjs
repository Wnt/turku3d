// In-game check of the streamed music: nothing is requested before the game is playable, free play starts a track
// about five seconds in, pause brings the menu theme and back, N moves on, a delivery run plays the chase track with
// the deadline lift, mute pauses the stream. Reads audio.snapshot().music and the network requests.
//   BROWSER_CDP=... BASE=... node check-music.mjs OUT.json [new|multiplayer]
import fs from 'node:fs';
import { BASE, connect, log, openGame, sleep } from './lib.mjs';

const [out, option = 'new'] = process.argv.slice(2);
const browser = await connect();
const g = await openGame(browser, { option, query: `&traffic=0&walkers=0${option === 'multiplayer' ? `&room=sf-music-${Date.now() % 100000}` : ''}` });
const { context, page, errors, audioRequests, marks } = g;
const result = { base: BASE, option, problems: [] };
const music = () => page.evaluate(() => { const s = window.__game.audio.snapshot(); return { ...s.music, track: s.music.playing?.track?.id ?? null, errors: s.errors }; });
const waitFor = async (test, seconds) => { const end = Date.now() + seconds * 1000; let m; do { m = await music(); if (test(m)) return m; await sleep(250); } while (Date.now() < end); return m; };
const musicRequests = () => audioRequests.filter(r => /music\//.test(r.file));
try {
  result.version = await page.evaluate(() => fetch('version.json').then(r => r.json()).catch(() => null));
  result.marks = marks;
  // 1: free play: the first stream opens only after the game is playable.
  const first = await waitFor(m => m.track && m.playing.playing, 20);
  result.firstStream = { at: g.seconds(), requests: musicRequests(), state: first.state, track: first.track };
  log('first stream', JSON.stringify(result.firstStream), 'marks', JSON.stringify(marks));
  await sleep(2500);
  const a = await music(); await sleep(2000); const b = await music();
  result.advancing = { from: a.playing.position, to: b.playing.position };
  // The served file: an Ogg stream with ranges.
  result.file = await page.evaluate(async () => { const r = await fetch('assets/audio/music/synthwave.ogg', { headers: { Range: 'bytes=0-99' } }); return { status: r.status, type: r.headers.get('content-type'), range: r.headers.get('content-range'), bytes: (await r.arrayBuffer()).byteLength }; });
  // 2: pause: the menu theme; resume: free play from where it was.
  const position = (await music()).playing.position;
  await page.keyboard.press('Escape');
  result.paused = await waitFor(m => m.track === 'menu', 8);
  await sleep(2500);
  await page.keyboard.press('Escape');
  result.resumed = await waitFor(m => m.track === first.track && m.playing.playing, 8);
  result.resumedFrom = { before: position, after: result.resumed.playing.position };
  log('pause', result.paused.track, 'resume', result.resumed.track, JSON.stringify(result.resumedFrom));
  // 3: N: the next track of free play.
  await sleep(1000); await page.keyboard.press('KeyN');
  result.next = await waitFor(m => m.track && m.track !== first.track && m.playing.playing, 8);
  // 4: a delivery run, driven at the music system (the play session only forwards changes of state): the chase
  // track from the top; the deadline lifts the rate.
  await page.evaluate(() => { const m = window.__game.audio.music; m.setState('mission'); });
  result.mission = await waitFor(m => m.track === 'dnb', 8);
  await page.evaluate(() => window.__game.audio.music.setDeadline(1)); await sleep(700);
  result.deadline = await music();
  result.elementRate = await page.evaluate(() => [...window.__game.audio.music.voices.values()].find(v => v.track.id === 'dnb')?.element.playbackRate ?? null);
  await page.evaluate(() => window.__game.audio.music.deliver());
  result.delivered = await waitFor(m => m.playing.state === 'explore' && m.track && m.track !== 'dnb' && m.playing.rate === 1, 8);
  log('mission', result.mission.track, 'rate at deadline', result.deadline.playing.rate, 'element', result.elementRate, 'after delivery', result.delivered.track);
  // 5: the mute button and the volume slider of the pause menu's audio page (their handlers).
  await page.evaluate(() => window.__game.audio.setMusicMuted(true));
  result.muted = await waitFor(m => !m.playing.playing && m.playing.fading === 0, 8);
  await page.evaluate(() => { window.__game.audio.setMusicMuted(false); window.__game.audio.setMusicVolume(0.3); });
  result.unmuted = await waitFor(m => m.playing.playing, 8);
  await page.evaluate(() => window.__game.audio.setMusicVolume(0.42));
  result.requests = musicRequests();
  result.streams = (await music()).playing.streams;
  const p = result.problems;
  const before = result.firstStream.requests.filter(r => r.s <= marks.playable);
  if (before.length) p.push(`music requested before playable: ${before.map(r => r.file).join(', ')}`);
  if (!first.track) p.push('no track in free play within 20 s');
  if (!(result.advancing.to > result.advancing.from)) p.push('the stream does not advance');
  if (![200, 206].includes(result.file.status) || !/ogg/.test(result.file.type ?? '')) p.push(`music file: ${JSON.stringify(result.file)}`);
  if (result.paused.track !== 'menu') p.push('pause did not bring the menu theme');
  if (result.resumed.track !== first.track || result.resumedFrom.after < result.resumedFrom.before - 0.5) p.push('free play did not resume where it was');
  if (!result.next.track || result.next.track === first.track) p.push('N did not move to the next track');
  if (result.mission.track !== 'dnb') p.push('the delivery run did not start the chase track');
  if (Math.abs(result.deadline.playing.rate - 1.06) > 0.001 || Math.abs((result.elementRate ?? 0) - 1.06) > 0.001) p.push(`deadline rate ${result.deadline.playing.rate} / element ${result.elementRate}`);
  if (result.delivered.playing.state !== 'explore' || result.delivered.track === 'dnb') p.push('delivery did not return to free play');
  if (result.muted.playing.playing) p.push('mute did not pause the stream');
  if (!result.unmuted.playing.playing || result.unmuted.volume !== 0.3) p.push('unmute or the volume did not apply');
  if (result.unmuted.errors.length) p.push('audio errors: ' + result.unmuted.errors.join('; '));
  if (result.unmuted.playing.error) p.push('music error: ' + result.unmuted.playing.error);
  if (errors.length) p.push('page errors: ' + errors.slice(0, 3).join(' | '));
} catch (e) { result.problems.push('check failed: ' + e.message.split('\n')[0]); }
if (out) fs.writeFileSync(out, JSON.stringify(result, null, 1));
console.log(result.problems.length ? 'PROBLEMS\n  ' + result.problems.join('\n  ') : 'OK');
await context.close();
process.exit(result.problems.length ? 1 : 0);
