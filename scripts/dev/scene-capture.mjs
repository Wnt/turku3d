#!/usr/bin/env node
// The one maintained capture command: fixed gameplay views with matched settings, appearance variants in the same
// session, a short walking clip, checks, timings and a comparison against earlier captures (frozen city, previous
// lab, an earlier local run). It replaces per-feature capture/check/probe scripts; extend it instead of copying it.
//
//   scripts/dev/scene-capture.mjs --views scripts/dev/views/riverside-office.json --out "$TURKU3D_WORK/captures/NAME"
//     [--base http://localhost:$PORT/] [--cdp http://127.0.0.1:PORT] [--mode play|render] [--entry solo|multiplayer] [--reload]
//     [--variant NAME=patch.json]... [--baseline DIR]... [--clip SECONDS|0] [--checks errors,camera,resolution,player,scene]
//     [--expect-release SHA] [--settle-ms 6000] [--label TEXT]
//   (The preview browser runs muted: a capture never plays the game's audio.)
//
// A view is a world camera {position, quaternion, fov} in engine metres, a surveyed pose {E, N, H, yaw, pitch, roll, fov}
// (grid bearing and vertical fov in degrees, as qa/views/views.jsonl), or `reference: ID` from the set's `references`
// registry: the render then matches that photo's camera, and the player stands 4 m behind it, out of view.
// A view's optional `group` (an area type, a location) is kept for the judge's per-group means.
// The set's `scene` is the prepared scene to load (default office); `scene: null` is the whole city of a client without
// prepared scenes, the frozen Gen 1 city (views/pilot-locations.json).
//
// --mode render draws the prepared scene alone and still: the game's QA camera mode (?cam=, game/src/main.ts runQa)
// with the same renderer, materials, vegetation, sky and post-processing, but no player, physics, audio, traffic,
// walkers, birds, HUD or multiplayer, and the water and wind frozen at time 0 (it refuses a client that is not still),
// at the full viewport resolution and with its own readiness (__qaReady). Judge captures use it (scene-judge.mjs).
// --mode play (the default) is the game itself, for the walking clip and gameplay evidence.
//
// The browser is the preview session (scripts/dev/preview-session.mjs) unless --cdp names another one. A page that
// already shows the requested game is reused as it is: no reload, so variants compare without restarting the game.
// Readiness is explicit (window.__render.quiet); builds without it (the frozen city, older lab releases) fall back
// to --settle-ms and the manifest says so. Images, clips and comparisons stay outside Git.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { previewState, touchPreview } from './preview-session.mjs';
import { port } from '../../tools/dev/environment.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(path.join(repo, 'game/package.json'))('playwright');
const argv = process.argv.slice(2);
const opt = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const many = name => argv.flatMap((a, i) => a === name ? [argv[i + 1]] : []);
const QUIET_FRAMES = 24;
const { CITIES } = await import('../../game/src/core/city.ts');
const THREE = createRequire(path.join(repo, 'game/package.json'))('three');

/** A surveyed pose as the game's QA camera applies it (game/src/main.ts applyQaCamera): Euler YXZ =
 *  (pitch, -heading, -roll), vertical fov, engine x = E - E0, y = H, z = -(N - N0). */
export function poseCamera(pose, city = 'turku') {
  const { E: E0, N: N0 } = CITIES[city].origin, d = Math.PI / 360;
  const [x, y, z] = [pose.pitch * d, -pose.yaw * d, -(pose.roll ?? 0) * d];
  const [c1, c2, c3, s1, s2, s3] = [Math.cos(x), Math.cos(y), Math.cos(z), Math.sin(x), Math.sin(y), Math.sin(z)];
  return { position: [pose.E - E0, pose.H, -(pose.N - N0)], fov: pose.fov,
    quaternion: [s1 * c2 * c3 + c1 * s2 * s3, c1 * s2 * c3 - s1 * c2 * s3, c1 * c2 * s3 - s1 * s2 * c3, c1 * c2 * c3 + s1 * s2 * s3] };
}

/** The inverse of poseCamera: a world camera as the QA mode's surveyed pose. */
export function cameraPose(camera, city = 'turku') {
  const { E: E0, N: N0 } = CITIES[city].origin, deg = 180 / Math.PI;
  const e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(...camera.quaternion), 'YXZ');
  return { E: camera.position[0] + E0, N: N0 - camera.position[2], H: camera.position[1],
    yaw: ((-e.y * deg) % 360 + 360) % 360, pitch: e.x * deg, roll: -e.z * deg, fov: camera.fov };
}

export function readViews(file) {
  const views = JSON.parse(fs.readFileSync(file, 'utf8'));
  const finite = v => Array.isArray(v) && v.every(Number.isFinite);
  if (views.schema !== 'turku-capture-views/1' || !Array.isArray(views.views) || !views.views.length) throw new Error(`${file}: not a turku-capture-views/1 set`);
  if (!finite(views.viewport) || views.viewport.length !== 2 || !(views.renderScale > 0 && views.renderScale <= 1) || !views.time) throw new Error(`${file}: viewport, renderScale and time are required`);
  const registry = views.references ? new Map(fs.readFileSync(path.resolve(repo, views.references), 'utf8').split('\n').filter(Boolean)
    .map(line => JSON.parse(line)).map(row => [row.id, row])) : new Map();
  for (const v of views.views) {
    if (v.reference) {
      const row = registry.get(v.reference);
      if (!row) throw new Error(`${file}: view ${v.id}: reference ${v.reference} is not in ${views.references ?? 'a references registry'}`);
      v.pose = row.pose;
      v.photo = { id: row.id, image: row.image, kind: row.source.kind, date: row.capture_date, poseConfidence: row.pose_confidence?.level ?? null };
    }
    if (v.pose) v.camera = poseCamera(v.pose, views.city);
  }
  for (const v of views.views) if (!/^[a-z0-9-]+$/.test(v.id ?? '') || !finite(v.camera?.position) || v.camera.position.length !== 3
    || !finite(v.camera?.quaternion) || v.camera.quaternion.length !== 4 || !Number.isFinite(v.camera?.fov)) throw new Error(`${file}: view ${v.id} needs id and camera {position, quaternion, fov}`);
  return views;
}

/** Settings two captures must share to be a controlled comparison. */
export function comparable(a, b) {
  const reasons = [];
  for (const key of ['quality', 'time', 'weather', 'renderScale']) if (JSON.stringify(a.settings[key]) !== JSON.stringify(b.settings[key])) reasons.push(`${key} differs`);
  if (JSON.stringify(a.settings.viewport) !== JSON.stringify(b.settings.viewport)) reasons.push('viewport differs');
  for (const view of a.views) {
    const other = b.views.find(v => v.id === view.id);
    if (!other) reasons.push(`view ${view.id} missing`);
    else if (JSON.stringify(other.requested) !== JSON.stringify(view.requested)) reasons.push(`view ${view.id} camera differs`);
  }
  return { controlled: !reasons.length, reasons };
}

export function sessionUrl(base, views, entry) {
  const url = new URL(base);
  if (entry === 'render') {
    // The QA camera mode starts at the first view; later views move it (__qaMove), so `cam` is not a startup difference.
    const p = views.views[0].pose ?? cameraPose(views.views[0].camera, views.city);
    url.searchParams.set('cam', [p.E, p.N, p.H, p.yaw, p.pitch, p.fov, p.roll ?? 0].map(v => +v.toFixed(6)).join(','));
    url.searchParams.set('time', views.time);
  }
  if (entry === 'solo') url.searchParams.set('solo', '1');
  if (views.quality) url.searchParams.set('quality', views.quality);
  if (entry !== 'render') { url.searchParams.set('hud', '0'); url.searchParams.set('governor', '0'); }
  if (views.scene && views.scene !== 'office') url.searchParams.set('region', views.scene);
  return url;
}

const ms = since => Math.round(performance.now() - since);

async function main() {
  const views = readViews(opt('--views', path.join(repo, 'scripts/dev/views/riverside-office.json')));
  const out = path.resolve(opt('--out', path.join(process.env.TURKU3D_WORK ?? '.', 'captures', new Date().toISOString().replace(/[:.]/g, '-'))));
  const base = opt('--base', `http://localhost:${port()}/`);
  const mode = opt('--mode', 'play');
  if (!['play', 'render'].includes(mode)) throw new Error(`--mode is play or render: ${mode}`);
  const entry = mode === 'render' ? 'render' : opt('--entry', /localhost|127\.0\.0\.1/.test(base) ? 'solo' : 'multiplayer');
  const checks = new Set(opt('--checks', 'errors,camera,resolution,player,scene').split(',').filter(Boolean));
  const clipSeconds = mode === 'render' ? 0 : Number(opt('--clip', String(views.walk?.seconds ?? 0)));
  const settleMs = Number(opt('--settle-ms', '6000'));
  const variants = many('--variant').map(spec => {
    const [name, file] = spec.split('=');
    if (!/^[a-z0-9-]+$/.test(name ?? '') || !file) throw new Error(`--variant wants NAME=patch.json: ${spec}`);
    return { name, file: path.resolve(file), patch: JSON.parse(fs.readFileSync(file, 'utf8')) };
  });
  const session = previewState();
  const cdp = opt('--cdp', session?.cdp);
  if (!cdp) throw new Error('No browser: start scripts/dev/preview-session.mjs start, or pass --cdp');
  if (!opt('--cdp')) touchPreview();
  fs.mkdirSync(out, { recursive: true });
  const started = performance.now(), timings = {};
  const manifest = { schema: 'turku-capture/1', label: opt('--label', null), started: new Date().toISOString(), base, mode, entry,
    settings: { quality: views.quality ?? null, time: views.time, weather: views.weather ?? null, viewport: views.viewport, renderScale: views.renderScale },
    viewsFile: path.relative(repo, path.resolve(opt('--views', path.join(repo, 'scripts/dev/views/riverside-office.json')))),
    timings, views: [], variants: [], checks: {}, errors: [], consoleErrors: [], failedRequests: [], readiness: null };
  const write = () => fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(manifest, null, 1) + '\n');

  const browser = await chromium.connectOverCDP(cdp);
  const url = sessionUrl(base, views, entry);
  const context = browser.contexts()[0] ?? await browser.newContext();
  // Another origin (city.lab, the frozen city) gets its own tab, closed afterwards: the local session stays as it is.
  let page = context.pages().find(p => p.url().startsWith(url.origin)) ?? null;
  const ownTab = !page && !!session && new URL(session.url).origin !== url.origin;
  page ??= ownTab ? await context.newPage() : context.pages()[0] ?? await context.newPage();
  page.on('pageerror', e => manifest.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') manifest.consoleErrors.push(m.text()); });
  page.on('response', r => { if (r.status() >= 400) manifest.failedRequests.push({ status: r.status(), url: r.url() }); });
  // ERR_ABORTED is a cancelled request (streamed audio, a replaced fetch), not a missing file.
  page.on('requestfailed', r => { const why = r.failure()?.errorText ?? 'failed'; if (!/ERR_ABORTED/.test(why)) manifest.failedRequests.push({ status: why, url: r.url() }); });
  try {
    await page.setViewportSize({ width: views.viewport[0], height: views.viewport[1] });
    const cdpSession = await context.newCDPSession(page);
    await cdpSession.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    // Reuse a live session that already runs the requested game; reload only when its startup parameters differ.
    let t = performance.now();
    const current = new URL(page.url() === 'about:blank' ? 'about:blank' : page.url());
    const sameStart = current.origin === url.origin && [...url.searchParams].every(([k, v]) => k === 'cam' ? current.searchParams.has('cam') : current.searchParams.get(k) === v)
      && (mode === 'render') === current.searchParams.has('cam');
    const live = sameStart && await page.evaluate(render => render ? typeof window.__qaMove === 'function' : !!window.__game?.ready, mode === 'render').catch(() => false);
    if (!live || argv.includes('--reload')) await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 60000 });
    manifest.reusedSession = live && !argv.includes('--reload');
    if (mode === 'render') {
      await page.waitForFunction(() => window.__qaReady === true || !!window.__qaError, null, { timeout: 240000 });
      const qaError = await page.evaluate(() => window.__qaError ?? null);
      if (qaError) throw new Error(`QA render mode: ${qaError}`);
    } else {
      await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
      const startScreen = await page.evaluate(() => window.__game.state().startScreen);
      if (startScreen) await page.getByText(entry === 'multiplayer' ? 'Multiplayer' : 'New game', { exact: true }).click();
      await page.waitForFunction(() => { const s = window.__game.state(); return !s.startScreen && !s.loading; }, null, { timeout: 240000 });
    }
    timings.loadMs = ms(t);
    manifest.source = await page.evaluate(async () => {
      const read = async url => { try { const r = await fetch(url, { cache: 'no-store' }); return r.ok && /json/.test(r.headers.get('content-type') ?? '') ? await r.json() : null; } catch { return null; } };
      const scene = window.__game?.coarse?.data;
      return { version: await read('/version.json'), build: await read('/__build'), three: window.__game?.three ?? null,
        scene: scene ? { id: scene.id, release: scene.release } : null, backend: window.__game?.backend ?? null };
    });
    if (mode === 'render') {
      // Judged and compared images come from a still scene: nothing that moves between two captures of one view.
      // The frozen whole-city build (Gen 1, every pilot location) still flies birds in its QA view: they are hidden here.
      manifest.still = await page.evaluate(() => {
        const birds = window.__dev?.birds;
        if (birds?.mesh) birds.mesh.visible = false;
        return { birds: !!birds && birds.mesh?.visible !== false, population: !!window.__game?.population,
          motion: new URLSearchParams(location.search).get('motion') === '1' };
      });
      const moving = Object.entries(manifest.still).filter(([, on]) => on).map(([k]) => k);
      if (moving.length) throw new Error(`render mode is not a still scene: ${moving.join(', ')} (an older client?)`);
    }
    manifest.readiness = mode === 'render' ? 'qa-ready (32 stable frames, everything loaded)'
      : await page.evaluate(() => typeof window.__render?.quiet === 'function') ? 'quiet-frames' : `fixed ${settleMs} ms (no __render.quiet in this build)`;

    const settle = async () => {
      const t0 = performance.now();
      if (mode === 'render') {
        await page.waitForFunction(() => window.__qaReady === true || !!window.__qaError, null, { timeout: 120000, polling: 100 });
        const qaError = await page.evaluate(() => window.__qaError ?? null);
        if (qaError) throw new Error(`QA render mode: ${qaError}`);
      } else if (manifest.readiness === 'quiet-frames') {
        // N quiet frames, all rendered after the change (a new camera pose streams and uploads only once drawn).
        const from = await page.evaluate(() => window.__render.quiet().frame);
        await page.waitForFunction(({ n, from }) => { const q = window.__render.quiet(); return q.frame - from >= n && q.quietFrames >= n; },
          { n: QUIET_FRAMES, from }, { timeout: 120000, polling: 100 });
      } else await page.waitForTimeout(settleMs);
      return ms(t0);
    };
    t = performance.now();
    manifest.applied = await page.evaluate(async ({ time, weather, scale, render }) => {
      const g = window.__game, r = window.__render, d = window.__dev;
      if (render) {
        // The time came with the URL; the QA mode draws at the full viewport (no adaptive resolution).
        if (weather && d?.setWeather) d.setWeather(weather, 0);
        return { mode: 'render', weather: !!d?.setWeather, scenePatch: !!d?.scene };
      }
      r.setTime(time);
      if (weather && d?.setWeather) d.setWeather(weather, 0);
      if (d?.fixResolution) d.fixResolution(scale);
      else { g.resolution.scale = scale; g.resolution.update = () => scale; r.renderer.setPixelRatio(scale); }
      return { fixResolution: !!d?.fixResolution, holdCamera: typeof g.holdCamera === 'function', scenePatch: !!d?.scene };
    }, { time: views.time, weather: views.weather, scale: views.renderScale, render: mode === 'render' });
    timings.settingsMs = ms(t);
    if (views.player && mode === 'play') {
      // The player stands where the views expect it (localhost teleport); elsewhere the spawn decides and the manifest says so.
      t = performance.now();
      manifest.player = await page.evaluate(async p => {
        const g = window.__game;
        let teleported = false, note = null;
        try { if (g.saveGame?.teleport) teleported = await g.saveGame.teleport(p.E, p.N, p.headingDeg); else note = 'no saveGame.teleport'; }
        catch (error) { note = String(error?.message ?? error); }
        const s = g.state();
        return { requested: p, teleported, note, actual: { E: s.E, N: s.N, H: s.H } };
      }, views.player);
      manifest.player.offsetM = Math.hypot(manifest.player.actual.E - views.player.E, manifest.player.actual.N - views.player.N);
      timings.playerMs = ms(t);
    }

    const hold = camera => mode === 'render' ? camera && page.evaluate(pose => window.__qaMove(pose), cameraPose(camera, views.city)) : page.evaluate(camera => {
      const g = window.__game, r = window.__render;
      if (g.holdCamera) return g.holdCamera(camera);
      // Older builds: hold the camera after the orbit camera's own update (the November 2026 capture method).
      window.__captureOrbit ??= g.orbit.update;
      g.orbit.update = camera ? function (...a) {
        window.__captureOrbit.apply(this, a);
        r.camera.position.fromArray(camera.position); r.camera.quaternion.fromArray(camera.quaternion);
        r.camera.fov = camera.fov; r.camera.updateProjectionMatrix(); r.camera.updateMatrixWorld(true);
      } : window.__captureOrbit;
    }, camera);
    const shoot = async (variant, view) => {
      if (view.pose && mode === 'play') {
        // The photo has no player in it: stand 4 m behind the camera, facing the same way.
        const r = view.pose.yaw * Math.PI / 180;
        view.player = await page.evaluate(async p => {
          try { return { teleported: await window.__game.saveGame?.teleport?.(p.E, p.N, p.headingDeg) ?? false }; }
          catch (error) { return { teleported: false, note: String(error?.message ?? error) }; }
        }, { E: view.pose.E - 4 * Math.sin(r), N: view.pose.N - 4 * Math.cos(r), headingDeg: view.pose.yaw });
      }
      await hold(view.camera);
      const settleMs = await settle();
      const t0 = performance.now();
      const actual = await page.evaluate(() => {
        const r = window.__render, c = r.camera;
        return { camera: { position: c.position.toArray(), quaternion: c.quaternion.toArray(), fov: c.fov },
          internal: [r.renderer.domElement.width, r.renderer.domElement.height], quiet: r.quiet?.() ?? null };
      });
      const hidden = await page.evaluate(() => [...document.querySelectorAll('body > *:not(canvas)')].map(e => {
        const was = e.style.getPropertyValue('display'); e.style.setProperty('display', 'none', 'important'); return was;
      }));
      const file = `${variant}/${view.id}.png`;
      fs.mkdirSync(path.join(out, variant), { recursive: true });
      await page.screenshot({ path: path.join(out, file) });
      await page.evaluate(was => [...document.querySelectorAll('body > *:not(canvas)')].forEach((e, i) => {
        if (was[i]) e.style.setProperty('display', was[i]); else e.style.removeProperty('display');
      }), hidden);
      const residual = { positionM: Math.hypot(...view.camera.position.map((x, i) => x - actual.camera.position[i])),
        // 1 - |q·q'|: q and -q are the same rotation (the QA mode rebuilds it from Euler angles).
        quaternion: 1 - Math.abs(view.camera.quaternion.reduce((sum, x, i) => sum + x * actual.camera.quaternion[i], 0)),
        fov: Math.abs(view.camera.fov - actual.camera.fov) };
      return { id: view.id, file, requested: view.camera, actual: actual.camera, residual, internal: actual.internal, quiet: actual.quiet,
        settleMs, captureMs: ms(t0), ...(view.group ? { group: view.group } : {}), ...(view.pose ? { pose: view.pose, photo: view.photo ?? null, player: view.player } : {}) };
    };

    t = performance.now();
    for (const view of views.views) manifest.views.push(await shoot('current', view));
    timings.viewsMs = ms(t);
    for (const variant of variants) {
      const t0 = performance.now();
      const report = await page.evaluate(patch => window.__dev.scene.patch(patch), variant.patch);
      const applyMs = ms(t0);
      const shots = [];
      for (const view of views.views) shots.push(await shoot(variant.name, view));
      manifest.variants.push({ name: variant.name, patch: path.relative(repo, variant.file), report, applyMs,
        visibleMs: applyMs + shots[0].settleMs, views: shots });
      await page.evaluate(() => window.__dev.scene.restore());
    }
    await hold(null);
    write();

    if (clipSeconds > 0) {
      t = performance.now();
      await page.evaluate(async start => {
        const g = window.__game;
        // The walk starts where the views put the player (or the view set's own walk start), every capture alike.
        if (start && g.saveGame?.teleport) await g.saveGame.teleport(start.E, start.N, start.headingDeg);
      }, views.walk?.start ?? views.player ?? null);
      await settle();
      const frames = [];
      cdpSession.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
        frames.push({ data, t: metadata.timestamp });
        void cdpSession.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
      });
      await cdpSession.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1 });
      const key = views.walk?.key ?? 'w';
      await page.keyboard.down(key); await page.waitForTimeout(clipSeconds * 1000); await page.keyboard.up(key);
      await cdpSession.send('Page.stopScreencast');
      const dir = path.join(out, 'walk');
      fs.mkdirSync(dir, { recursive: true });
      frames.forEach((f, i) => fs.writeFileSync(path.join(dir, `${String(i).padStart(5, '0')}.jpg`), Buffer.from(f.data, 'base64')));
      const list = frames.map((f, i) => `file '${String(i).padStart(5, '0')}.jpg'\nduration ${((frames[i + 1]?.t ?? f.t + 1 / 30) - f.t).toFixed(4)}`).join('\n');
      fs.writeFileSync(path.join(dir, 'frames.txt'), list + '\n');
      manifest.clip = { seconds: clipSeconds, frames: frames.length, fps: frames.length > 1 ? +(frames.length / (frames.at(-1).t - frames[0].t)).toFixed(1) : 0,
        after: await page.evaluate(() => window.__game.state().feet ?? null) };
      try {
        execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', 'frames.txt', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
          '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-crf', '23', '../walk.mp4'], { cwd: dir });
        manifest.clip.file = 'walk.mp4';
        fs.rmSync(dir, { recursive: true, force: true });
      } catch (error) { manifest.clip.file = null; manifest.clip.note = `frames kept in walk/ (ffmpeg: ${error.message.split('\n')[0]})`; }
      timings.clipMs = ms(t);
    }

    // Checks: a failed check fails the command, the manifest keeps every result.
    // Scene content that failed to load (by URL) and GPU/loader errors; an unrelated 404 (a favicon) does not count.
    // (The local coarse service is optional for a prepared scene: its navigation 503 is not scene content.)
    const assetFailures = [...manifest.failedRequests.filter(r => /\/scenes\/|\.(?:glb|ktx2|col)(?:\?|$)|\/assets\/.*\.(?:png|jpg|webp)(?:\?|$)/.test(r.url)).map(r => `${r.status} ${r.url}`),
      ...manifest.consoleErrors.filter(x => /GLTFLoader|Couldn.t load texture|GPUValidationError|GPUOutOfMemoryError|device.{0,16}lost/i.test(x))];
    const shots = [...manifest.views, ...manifest.variants.flatMap(v => v.views)];
    const scale = mode === 'render' ? 1 : views.renderScale;
    const expected = [Math.round(views.viewport[0] * scale), Math.round(views.viewport[1] * scale)];
    if (checks.has('errors')) manifest.checks.errors = { ok: !manifest.errors.length && !assetFailures.length, pageErrors: manifest.errors.length, assetFailures };
    if (checks.has('camera')) manifest.checks.camera = { ok: shots.every(s => s.residual.positionM < 1e-6 && s.residual.quaternion < 1e-6 && s.residual.fov < 1e-6) };
    if (checks.has('resolution')) manifest.checks.resolution = { ok: shots.every(s => Math.abs(s.internal[0] - expected[0]) <= 1 && Math.abs(s.internal[1] - expected[1]) <= 1), expected };
    if (checks.has('player') && views.player && mode === 'play') manifest.checks.player = { ok: (manifest.player?.offsetM ?? Infinity) < 0.5, offsetM: manifest.player?.offsetM ?? null };
    if (checks.has('scene')) {
      // `scene: null` asks for the whole-city client (the frozen Gen 1 city): no prepared scene may be loaded.
      const scene = manifest.source.scene, want = opt('--expect-release'), whole = views.scene === null;
      manifest.checks.scene = { ok: whole ? !scene : !!scene && (!views.scene || scene.id === views.scene) && (!want || scene.release.startsWith(want)),
        scene, expected: { id: whole ? 'none (whole city)' : views.scene ?? null, release: want ?? null } };
    }
  } catch (error) {
    manifest.failure = String(error?.stack ?? error);
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
  } finally {
    timings.totalMs = ms(started);
    manifest.finished = new Date().toISOString();
    manifest.baselines = many('--baseline').map(dir => {
      const other = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
      return { dir: path.resolve(dir), label: other.label, source: other.source, ...comparable(manifest, other) };
    });
    write();
    compare(out, manifest);
    if (ownTab) await page.close().catch(() => {});
    await browser.close().catch(() => {}); // Disconnects; the preview browser itself keeps running.
  }
  const failed = Object.entries(manifest.checks).filter(([, c]) => !c.ok).map(([k]) => k);
  console.log(JSON.stringify({ out, timings, readiness: manifest.readiness, failed, failure: manifest.failure ?? null,
    variants: manifest.variants.map(v => ({ name: v.name, applyMs: v.applyMs, visibleMs: v.visibleMs })),
    baselines: manifest.baselines.map(b => ({ dir: b.dir, controlled: b.controlled, reasons: b.reasons })) }));
  if (manifest.failure || failed.length) process.exitCode = 1;
}

/** compare.html: one row per view, columns for every baseline, this capture and its variants (plus the clips);
 *  compare.jpg: the same grid as one image when ffmpeg is available. */
function compare(out, manifest) {
  const columns = [...manifest.baselines.map(b => {
    const other = JSON.parse(fs.readFileSync(path.join(b.dir, 'manifest.json'), 'utf8'));
    return { title: `${b.label ?? path.basename(b.dir)}${b.controlled ? '' : ' (not controlled: ' + b.reasons.join(', ') + ')'}`,
      files: Object.fromEntries(other.views.map(v => [v.id, path.join(b.dir, v.file)])), clip: other.clip?.file ? path.join(b.dir, other.clip.file) : null };
  }), { title: manifest.label ?? 'current', files: Object.fromEntries(manifest.views.map(v => [v.id, path.join(out, v.file)])), clip: manifest.clip?.file ? path.join(out, manifest.clip.file) : null },
  ...manifest.variants.map(v => ({ title: `variant ${v.name} (${v.visibleMs} ms to visible)`, files: Object.fromEntries(v.views.map(s => [s.id, path.join(out, s.file)])), clip: null }))];
  const ids = manifest.views.map(v => v.id);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const rel = f => path.relative(out, f);
  fs.writeFileSync(path.join(out, 'compare.html'), `<!doctype html><meta charset="utf-8"><title>Capture comparison</title>
<style>body{font:14px system-ui;margin:16px;background:#111;color:#eee}table{border-collapse:collapse}td,th{padding:4px;vertical-align:top}img,video{width:480px;display:block}</style>
<h1>${esc(manifest.label ?? 'Capture')} · ${esc(manifest.settings.time)} · ${esc(manifest.settings.weather)} · ${manifest.settings.viewport.join('×')} @ ${manifest.settings.renderScale}</h1>
<table><tr><th></th>${columns.map(c => `<th>${esc(c.title)}</th>`).join('')}</tr>
${ids.map(id => `<tr><th>${esc(id)}</th>${columns.map(c => `<td>${c.files[id] ? `<img src="${esc(rel(c.files[id]))}">` : '—'}</td>`).join('')}</tr>`).join('\n')}
<tr><th>walk</th>${columns.map(c => `<td>${c.clip ? `<video src="${esc(rel(c.clip))}" controls muted loop></video>` : '—'}</td>`).join('')}</tr></table>\n`);
  const cells = ids.flatMap(id => columns.map(c => c.files[id] && fs.existsSync(c.files[id]) ? c.files[id] : null));
  if (cells.some(c => !c) || cells.length < 2) return;
  const w = columns.length, inputs = cells.flatMap(f => ['-i', f]);
  const scaled = cells.map((_, i) => `[${i}:v]scale=720:-2[s${i}]`).join(';');
  const layout = cells.map((_, i) => `${(i % w) ? Array.from({ length: i % w }, (_, k) => `w${k}`).join('+') : 0}_${Math.floor(i / w) ? Array.from({ length: Math.floor(i / w) }, (_, k) => `h${k * w}`).join('+') : 0}`).join('|');
  try {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...inputs, '-filter_complex',
      `${scaled};${cells.map((_, i) => `[s${i}]`).join('')}xstack=inputs=${cells.length}:layout=${layout}`, '-q:v', '3', path.join(out, 'compare.jpg')]);
  } catch { /* compare.html remains the comparison */ }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
