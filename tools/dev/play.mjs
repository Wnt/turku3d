#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { spawnSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const aoi = ['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1'];
export function settings(env = process.env) {
  const work = path.resolve(env.TURKU3D_WORK ?? path.join(os.homedir(), 'turku3d/data/build/main'));
  const port = Number(env.TURKU3D_PORT_BASE ?? 5170);
  if (!Number.isInteger(port) || port < 1024 || port > 65526) throw new Error('TURKU3D_PORT_BASE must be an integer in 1024..65526');
  return { work, port, browser: path.resolve(env.TURKU3D_BROWSER_DIR ?? path.join(work, 'browser')), game: path.join(work, 'dist/game'), tiles: path.resolve(env.TILE_DIST ?? path.join(work, 'dist/tiles')),
    report: path.join(work, 'reports/playtest'), cache: path.join(work, 'cache/play') };
}

/** Include content, names, additions and removals, including uncommitted edits. */
export function fingerprint(root, names) {
  const hash = createHash('sha256');
  function visit(name) {
    const full = path.join(root, name);
    hash.update(name); hash.update('\0');
    if (fs.statSync(full).isDirectory()) for (const child of fs.readdirSync(full).sort()) visit(path.join(name, child));
    else hash.update(fs.readFileSync(full));
  }
  names.forEach(visit);
  return hash.digest('hex');
}
export function inspectTiles(root) {
  if (!fs.existsSync(path.join(root, 'manifest.json'))) return { missing: aoi };
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
  const missing = aoi.filter(id => !manifest.visual?.[id]);
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) {
    if (!manifest.collision?.[`c_${i}_${j}`]) {
      const parent = `t0_${Math.floor(i / 4)}_${Math.floor(j / 4)}`;
      if (!missing.includes(parent)) missing.push(parent);
    }
  }
  for (const entry of [manifest.tileset, ...Object.values(manifest.visual ?? {}), ...Object.values(manifest.collision ?? {})]) {
    if (!entry?.url || !entry.sha256) throw new Error(`Invalid tile manifest in ${root}`);
    const file = path.resolve(root, entry.url);
    if (!file.startsWith(path.resolve(root) + path.sep)) throw new Error('Tile path escapes its dist directory');
    if (!fs.existsSync(file) || createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== entry.sha256) {
      throw new Error(`Missing or corrupt tile artifact: ${file}. Rebuild with make tile-build T="${aoi.join(' ')}".`);
    }
  }
  return { missing, manifest };
}
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: repo, stdio: 'inherit', ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed (${result.status ?? result.signal})`);
}
export function prepare(s) {
  fs.mkdirSync(s.cache, { recursive: true });
  const env = { ...process.env, TURKU3D_WORK: s.work, TURKU3D_PORT_BASE: String(s.port), npm_config_cache: path.join(s.work, 'cache/npm') };
  const tiles = inspectTiles(s.tiles);
  if (tiles.missing.length) {
    if (process.env.TILE_DIST) throw new Error('TILE_DIST must contain a complete four-tile AOI; it is never overwritten automatically');
    run('make', ['tile-fetch'], { env });
    run('make', ['tile-build', `T=${tiles.missing.join(' ')}`], { env });
    if (inspectTiles(s.tiles).missing.length) throw new Error('Tile build did not produce the full AOI');
  } else console.log('Reusing verified AOI tiles.');
  const game = path.join(repo, 'game');
  const depsHash = fingerprint(game, ['package.json', 'package-lock.json']);
  const depsStamp = path.join(s.cache, 'dependencies');
  if (!fs.existsSync(path.join(game, 'node_modules/.package-lock.json')) || !fs.existsSync(depsStamp) || fs.readFileSync(depsStamp, 'utf8') !== depsHash) {
    run('npm', ['ci', '--no-audit', '--no-fund'], { cwd: game, env });
    fs.writeFileSync(depsStamp, depsHash);
  }
  const sourceHash = fingerprint(game, ['src', 'public', 'index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts']);
  const stamp = path.join(s.cache, 'game.json');
  let cached;
  try { cached = JSON.parse(fs.readFileSync(stamp, 'utf8')); } catch { /* First build. */ }
  const intact = cached?.source === sourceHash && fs.existsSync(s.game) && fingerprint(s.game, ['.']) === cached.output;
  if (!intact) {
    run('npm', ['run', 'build', '--', '--outDir', s.game, '--emptyOutDir'], { cwd: game, env });
    fs.writeFileSync(stamp, JSON.stringify({ source: sourceHash, output: fingerprint(s.game, ['.']) }));
  } else console.log('Reusing verified game build.');
}
async function main() {
  const mode = process.argv[2] ?? 'play';
  if (!['play', 'prepare', 'test'].includes(mode)) throw new Error('Usage: play.mjs play|prepare|test [Playwright arguments]');
  const s = settings();
  const started = performance.now();
  prepare(s);
  console.log(`Ready in ${((performance.now() - started) / 1000).toFixed(2)} s`);
  if (mode === 'prepare') return;
  if (mode === 'test') {
    // Keep the latest page at a predictable path and archive earlier evidence by commit and run time.
    const previous = path.join(s.report, 'report.json');
    if (fs.existsSync(previous)) {
      const report = JSON.parse(fs.readFileSync(previous, 'utf8'));
      const archive = path.join(s.work, 'reports/playtest-history', `${report.commit.slice(0, 12)}-${report.date.replaceAll(/[^0-9TZ]/g, '')}`);
      fs.mkdirSync(path.dirname(archive), { recursive: true });
      fs.renameSync(s.report, archive);
    }
    const result = spawnSync(path.join(repo, 'game/node_modules/.bin/playwright'), ['test', '-c', 'playwright.playtest.config.ts', ...process.argv.slice(3)], {
      cwd: path.join(repo, 'game'), stdio: 'inherit', env: { ...process.env, TURKU3D_WORK: s.work, TURKU3D_PORT_BASE: String(s.port),
        PLAY_GAME_DIST: s.game, TILE_DIST: s.tiles, PLAYTEST_REPORT: s.report },
    });
    console.log(`Playtest report: ${path.join(s.report, 'index.html')}`);
    if (result.error) throw result.error;
    process.exitCode = result.status ?? 1;
    return;
  }
  const server = spawn(process.execPath, [path.join(repo, 'tools/dev/serve-dist.mjs'), '--root', s.game, '--port', String(s.port), '--mount', `/tiles=${s.tiles}`], { stdio: ['ignore', 'pipe', 'inherit'] });
  let announced = false;
  server.stdout.on('data', data => {
    process.stdout.write(data);
    if (!announced && data.toString().includes('serve-dist:')) {
      announced = true;
      console.log(`\nPlay Turku: http://127.0.0.1:${s.port}/?tiles=/tiles/tileset.json\nPress Ctrl-C to stop this server.`);
    }
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.kill(signal));
  server.on('error', error => { console.error(error.message); process.exitCode = 1; });
  server.on('exit', (code, signal) => { process.exitCode = signal === 'SIGINT' || signal === 'SIGTERM' ? 0 : code ?? 1; });
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
