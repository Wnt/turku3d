#!/usr/bin/env node
// Persistent Chrome servers for the landing queue. The queue owns this process.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

function options(args) {
  const result = { spec: [] };
  for (let i = 0; i < args.length; i++) {
    if (!args[i].startsWith('--')) throw new Error(`unexpected argument: ${args[i]}`);
    const key = args[i].slice(2);
    if (key === 'spec') result.spec.push(args[++i]);
    else result[key] = args[++i];
  }
  for (const key of ['repo', 'work', 'profile', 'port']) if (!result[key]) throw new Error(`missing --${key}`);
  result.repo = path.resolve(result.repo);
  result.work = path.resolve(result.work);
  result.profile = path.resolve(result.profile);
  result.port = Number(result.port);
  if (!Number.isInteger(result.port) || result.port < 1024 || result.port + 4 > 65535) throw new Error('invalid port base');
  return result;
}

const args = options(process.argv.slice(3));
const stateFile = path.join(args.work, 'cache/warm-browser.json');
const requireGame = createRequire(pathToFileURL(path.join(args.repo, 'game/package.json')));
const { chromium } = requireGame('playwright');

async function serve() {
  const published = process.env.TURKU3D_DATA;
  if (!published) throw new Error('TURKU3D_DATA is required');
  const temp = path.join(args.profile, 'warm-browser');
  fs.mkdirSync(temp, { recursive: true });
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  const server = spawn(process.execPath, [path.join(args.repo, 'tools/dev/serve-dist.mjs'),
    '--root', path.join(published, 'live/game'), '--port', String(args.port + 1),
    '--mount', `/tiles=${path.join(published, 'live/tiles')}`], { env: process.env, stdio: 'ignore' });
  const browsers = [];
  const clients = [];
  const close = async () => {
    fs.rmSync(stateFile, { force: true });
    await Promise.allSettled(clients.map(client => client.close()));
    await Promise.allSettled(browsers.map(browser => browser.close()));
    server.kill('SIGTERM');
  };
  process.once('SIGTERM', () => { void close().then(() => process.exit(0)); });
  process.once('SIGINT', () => { void close().then(() => process.exit(0)); });
  try {
    const endpoints = {};
    for (const [backend, offset] of [['webgpu', 3], ['webgl', 4]]) {
      const browser = await chromium.launchServer({ channel: 'chrome', headless: true,
        host: '127.0.0.1', port: args.port + offset,
        env: { ...process.env, TMPDIR: temp, TMP: temp, TEMP: temp },
        args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist'] });
      browsers.push(browser);
      endpoints[backend] = browser.wsEndpoint();
    }
    const state = { pid: process.pid, repo: args.repo, work: args.work, profile: args.profile,
      port: args.port, endpoints };
    const temporary = `${stateFile}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(state, null, 2) + '\n', { mode: 0o600 });
    fs.renameSync(temporary, stateFile);
    process.stdout.write(JSON.stringify({ event: 'ready', pid: process.pid, stateFile }) + '\n');
    // Keep one loaded page per backend alive to retain Chrome's HTTP and shader caches.
    for (const backend of ['webgpu', 'webgl']) {
      const client = await chromium.connect(endpoints[backend]);
      clients.push(client);
      const context = await client.newContext({ viewport: { width: 640, height: 360 } });
      await context.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
      const page = await context.newPage();
      const url = `http://127.0.0.1:${args.port + 1}/?backend=${backend}&tiles=/tiles/tileset.json&qa=1&cam=23459668,6704254,8,0,-15,60`;
      try {
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await page.waitForFunction(() => window.__qaReady || window.__qaError, null, { timeout: 120000 });
        process.stdout.write(JSON.stringify({ event: 'warmed', backend, result: await page.evaluate(() => window.__qaError ?? 'ready') }) + '\n');
      } catch (error) {
        process.stdout.write(JSON.stringify({ event: 'warm-error', backend, error: String(error) }) + '\n');
      }
    }
    await new Promise(() => {});
  } finally {
    await close();
  }
}

async function run() {
  if (!['webgpu', 'webgl'].includes(args.backend)) throw new Error('--backend must be webgpu or webgl');
  if (!args.spec.length) throw new Error('at least one --spec is required');
  const state = JSON.parse(fs.readFileSync(args['service-state'] ?? stateFile, 'utf8'));
  const endpoint = state.endpoints[args.backend];
  if (!endpoint) throw new Error(`warm ${args.backend} endpoint is missing`);
  const start = performance.now();
  const output = path.join(args.work, 'reports', `warm-browser-${args.backend}-${process.pid}.json`);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const command = [path.join(args.repo, 'game/node_modules/.bin/playwright'), 'test', '-c', 'playwright.tile.config.ts',
    '--project', args.backend, '--reporter=json', ...(args.grep ? ['--grep', args.grep] : []), ...args.spec];
  const env = { ...process.env, TURKU3D_WORK: args.work, TURKU3D_BROWSER_DIR: args.profile,
    TURKU3D_PORT_BASE: String(args.port), TILE_DIST: path.join(args.work, 'dist/tiles'),
    [`TURKU3D_BROWSER_WS_${args.backend.toUpperCase()}`]: endpoint };
  const child = spawn(command[0], command.slice(1), { cwd: path.join(args.repo, 'game'), env, stdio: ['ignore', 'pipe', 'inherit'] });
  const chunks = [];
  child.stdout.on('data', chunk => chunks.push(chunk));
  const code = await new Promise(resolve => child.once('exit', resolve));
  fs.writeFileSync(output, Buffer.concat(chunks));
  const report = JSON.parse(Buffer.concat(chunks).toString());
  const tests = [];
  const collect = suite => {
    for (const spec of suite.specs ?? []) for (const test of spec.tests ?? []) {
      tests.push({ file: spec.file, title: spec.title, backend: test.projectName,
        seconds: ((test.results ?? []).at(-1)?.duration ?? 0) / 1000,
        status: (test.results ?? []).at(-1)?.status ?? 'not-run' });
    }
    for (const childSuite of suite.suites ?? []) collect(childSuite);
  };
  for (const suite of report.suites ?? []) collect(suite);
  process.stdout.write(JSON.stringify({ backend: args.backend, checked: args.spec, seconds: (performance.now() - start) / 1000,
    expected: report.stats.expected, unexpected: report.stats.unexpected, tests, report: output }) + '\n');
  process.exitCode = code || report.stats.unexpected || !tests.length ? 1 : 0;
}

try {
  if (process.argv[2] === 'serve') await serve();
  else if (process.argv[2] === 'run') await run();
  else throw new Error('usage: warm_browser.mjs serve|run --repo PATH --work PATH --profile PATH --port BASE [--backend webgpu|webgl --spec FILE]');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
