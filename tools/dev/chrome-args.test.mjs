import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromeArgs, chromeIgnoreDefaultArgs } from './chrome-args.mjs';
import { checkChromeLaunches } from './check-chrome-launches.mjs';

test('merges every disable-features switch without mutating caller arguments', () => {
  const args = ['--headless', '--disable-features=A,B', '--disable-features', 'B,C'];
  const result = chromeArgs(args);
  assert.equal(result[0], '--headless');
  assert.equal(result.filter(arg => arg.startsWith('--disable-features=')).length, 1);
  assert(result[1].endsWith(',A,B,C'));
  assert(result[1].includes('MacAppCodeSignClone'));
  assert.equal(args.length, 4);
  assert.deepEqual(chromeArgs(chromeArgs(args)), chromeArgs(args));
  assert(chromeArgs()[0].startsWith('--disable-features=MacAppCodeSignClone,'));
});

test('policy checks each launch, shared args, configs and embedded previews', () => {
  const prelude = "import { chromeArgs, chromeIgnoreDefaultArgs } from './chrome-args.mjs'; import {chromium} from 'playwright';";
  const safe = 'chromium.launch({ignoreDefaultArgs:chromeIgnoreDefaultArgs,args:chromeArgs(["--headless"])});';
  assert.deepEqual(checkChromeLaunches('a.mjs', prelude + safe), []);
  assert.equal(checkChromeLaunches('a.mjs', prelude + safe + 'chromium.launch({});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + 'chromium.launch({args:[]});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + 'chromium.launch({args:chromeArgs()});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', safe).length, 1);
  assert.deepEqual(checkChromeLaunches('playwright.config.ts', prelude + 'const args=chromeArgs(); defineConfig({use:{launchOptions:{ignoreDefaultArgs:chromeIgnoreDefaultArgs,args}}});'), []);
  assert.equal(checkChromeLaunches('playwright.config.ts', "defineConfig({use:{channel:'chrome'}})").length, 1);
  assert.equal(checkChromeLaunches('a.py', 'script = r"""chromium.launchPersistentContext(profile,{channel:"chrome"});"""').length, 1);
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
    for (const feature of features) assert(chromeArgs()[0].split('=')[1].split(',').includes(feature));
  }
});
