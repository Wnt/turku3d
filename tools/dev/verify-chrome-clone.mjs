// Real-Chrome regression probe: sample while browsers run, since clean exit can remove a clone.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import http from 'node:http';
import { chromium } from '../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeExecutable } from './chrome-args.mjs';
import { isolateBrowser, port, reportsRoot } from './environment.mjs';

assert.equal(process.platform, 'darwin', 'This probe verifies the macOS Chrome clone regression');
const systemTemp = execFileSync('getconf', ['DARWIN_USER_TEMP_DIR'], { encoding: 'utf8' }).trim();
const cloneRoot = path.resolve(systemTemp, '../X/com.google.Chrome.code_sign_clone');
function clones() {
  try { return fs.readdirSync(cloneRoot).filter(name => name.startsWith('code_sign_clone.')).sort(); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}
const baseline = new Set(clones());
const appeared = new Set();
const sample = () => { for (const name of clones()) if (!baseline.has(name)) appeared.add(name); };
const timer = setInterval(sample, 50);
const report = { executable: chromeExecutable(), cloneRoot, baseline: [...baseline], phases: [] };
const server = http.createServer((_, response) => response.end('<title>WebGPU probe</title>'));
server.listen(port(4), '127.0.0.1');
await once(server, 'listening');
const out = path.join(reportsRoot, 'chrome-clone');
fs.mkdirSync(out, { recursive: true });
isolateBrowser();
try {
  const args = chromeArgs(['--headless=new', '--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--enable-automation', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${path.join(process.env.TURKU3D_BROWSER_DIR, 'clone-cdp')}`,
    `--remote-debugging-port=${port(3)}`, '--remote-debugging-address=127.0.0.1', 'about:blank']);
  const child = spawn(chromeExecutable(), args,
    { env: process.env, stdio: ['ignore', 'ignore', 'ignore'] });
  const exited = once(child, 'exit');
  let browser;
  try {
    const endpoint = `http://127.0.0.1:${port(3)}`;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { const response = await fetch(endpoint + '/json/version'); if (response.ok) break; } catch { /* Chrome is starting. */ }
      assert.equal(child.exitCode, null, 'CDP Chrome exited before accepting connections');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.connectOverCDP(endpoint);
    const cdp = await browser.newBrowserCDPSession();
    const command = await cdp.send('Browser.getBrowserCommandLine');
    assert(command.arguments.includes('--use-mock-keychain'));
    assert(command.arguments.includes('--password-store=basic'));
    assert(command.arguments.some(arg => arg.startsWith('--disable-features=') && arg.split('=')[1].split(',').includes('MacAppCodeSignClone')));
    const page = await browser.contexts()[0].newPage();
    await page.goto(`http://127.0.0.1:${port(4)}`);
    const gpu = await page.evaluate(async () => ({ available: !!navigator.gpu, adapter: !!await navigator.gpu?.requestAdapter() }));
    assert(gpu.available && gpu.adapter, 'headless CfT must provide a WebGPU adapter');
    sample();
    report.phases.push({ gpu, name: 'native Chrome CDP', version: browser.version(), args, newClones: [...appeared] });
    await cdp.send('Browser.close');
    await exited;
  } finally {
    await browser?.close();
    if (child.exitCode === null) { child.kill('SIGTERM'); await exited; }
  }
  sample();
  assert.equal(appeared.size, 0, 'CDP launch created a clone');
  const playwright = spawn(path.resolve('tools/music-lab/node_modules/.bin/playwright'),
    ['test', '-c', 'playwright.config.ts', 'tests/music.spec.ts', '--workers=1'],
    { cwd: path.resolve('tools/music-lab'), env: { ...process.env, DEBUG: 'pw:browser' }, stdio: 'inherit' });
  const [code] = await once(playwright, 'exit');
  sample();
  report.phases.push({ name: 'playwright music.spec.ts', exitCode: code, newClones: [...appeared] });
  assert.equal(code, 0, 'Playwright spec failed');
  assert.equal(appeared.size, 0, 'Playwright launch created a clone');

  console.log(JSON.stringify(report, null, 2));
} finally {
  clearInterval(timer);
  server.close();
  report.newClones = [...appeared];
  report.after = clones();
  fs.writeFileSync(path.join(out, 'verification.json'), JSON.stringify(report, null, 2) + '\n');
}
