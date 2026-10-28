import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from './chrome-args.mjs';
import { checkChromeLaunches } from './check-chrome-launches.mjs';

test('merges every disable-features switch without mutating caller arguments', () => {
  const args = ['--headless', '--disable-features=A,B', '--disable-features', 'B,C'];
  const result = chromeArgs(args);
  assert.equal(result[0], '--headless');
  assert.equal(result.filter(arg => arg.startsWith('--disable-features=')).length, 1);
  assert(result.at(-1).endsWith(',A,B,C'));
  assert(result.at(-1).includes('MacAppCodeSignClone'));
  assert(result.includes('--use-mock-keychain'));
  assert(result.includes('--password-store=basic'));
  assert.equal(chromeArgs(['--password-store=keychain']).filter(arg => arg.startsWith('--password-store=')).join(), '--password-store=basic');
  assert.equal(args.length, 4);
  assert.deepEqual(chromeArgs(chromeArgs(args)), chromeArgs(args));
  assert(chromeArgs().at(-1).startsWith('--disable-features=MacAppCodeSignClone,'));
});

test('policy checks each launch, shared args, configs and embedded previews', () => {
  const prelude = "import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from './chrome-args.mjs'; import {chromium} from 'playwright';";
  const safe = 'chromium.launch({executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,args:chromeArgs(["--headless"])});';
  assert.deepEqual(checkChromeLaunches('a.mjs', prelude + safe), []);
  assert.equal(checkChromeLaunches('a.mjs', prelude + safe + 'chromium.launch({});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + 'chromium.launch({args:[]});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + 'chromium.launch({args:chromeArgs()});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', safe).length, 1);
  assert.deepEqual(checkChromeLaunches('playwright.config.ts', prelude + 'const args=chromeArgs(); defineConfig({use:{launchOptions:{executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,args}}});'), []);
  assert.equal(checkChromeLaunches('playwright.config.ts', "defineConfig({use:{channel:'chrome'}})").length, 2);
  assert.equal(checkChromeLaunches('a.py', 'script = r"""chromium.launchPersistentContext(profile,{channel:"chrome"});"""').length, 2);
  assert.equal(checkChromeLaunches('a.mjs', prelude + safe.replace('executablePath:chromeExecutable(),', '')).length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + safe.replace('executablePath:chromeExecutable()', 'executablePath: "/tmp/chrome"')).length, 1);
  assert.deepEqual(checkChromeLaunches('native.mjs', prelude + 'const args=chromeArgs(); spawn(chromeExecutable(), args);'), []);
  assert.equal(checkChromeLaunches('native.mjs', prelude + 'const args=chromeArgs(); spawn("/tmp/chrome", args);').length, 1);
  assert.equal(checkChromeLaunches('a.sh', 'chrome --remote-debugging-port=9222').length, 1);
});

// Keep the versioned compatibility switch exact: a Playwright upgrade must update the helper.
test('preserves installed Playwright defaults while removing its original switch', () => {
  for (const location of ['../../game/node_modules', '../../qa/render/node_modules']) {
    const source = fs.readFileSync(new URL(`${location}/playwright-core/lib/coreBundle.js`, import.meta.url), 'utf8');
    const block = source.match(/disabledFeatures = \[([\s\S]*?)\]\.filter\(Boolean\)/)[1];
    const features = [...block.matchAll(/^\s*"([^"]+)"/gm)].map(m => m[1]);
    assert(features.length > 0);
    assert.deepEqual(chromeIgnoreDefaultArgs, [`--disable-features=${features.join(',')}`]);
    for (const feature of features) assert(chromeArgs().at(-1).split('=')[1].split(',').includes(feature));
  }
});

test('resolver rejects desktop Chrome, including symlink overrides', () => {
  const previous = process.env.TURKU3D_CHROME;
  const root = fs.mkdtempSync(new URL('./.chrome-resolver-', import.meta.url));
  try {
    const desktop = `${root}/Google Chrome.app/Contents/MacOS/Google Chrome`;
    fs.mkdirSync(desktop.slice(0, desktop.lastIndexOf('/')), { recursive: true });
    fs.writeFileSync(desktop, '');
    fs.chmodSync(desktop, 0o755);
    const alias = `${root}/alias`;
    fs.symlinkSync(desktop, alias);
    for (const executable of [desktop, alias]) {
      process.env.TURKU3D_CHROME = executable;
      assert.throws(chromeExecutable, /not desktop Google Chrome/);
    }
    process.env.TURKU3D_CHROME = 'relative/chrome';
    assert.throws(chromeExecutable, /absolute/);
    process.env.TURKU3D_CHROME = `${root}/missing`;
    assert.throws(chromeExecutable, /ENOENT/);
    const automation = `${root}/automation`;
    fs.writeFileSync(automation, '');
    fs.chmodSync(automation, 0o755);
    process.env.TURKU3D_CHROME = automation;
    assert.equal(chromeExecutable(), fs.realpathSync(automation));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    if (previous === undefined) delete process.env.TURKU3D_CHROME;
    else process.env.TURKU3D_CHROME = previous;
  }
});
