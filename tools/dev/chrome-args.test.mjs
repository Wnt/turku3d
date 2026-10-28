import test from 'node:test';
import assert from 'node:assert/strict';
import { chromeArgs } from './chrome-args.mjs';
import { checkChromeLaunches } from './check-chrome-launches.mjs';

test('merges every disable-features switch without mutating caller arguments', () => {
  const args = ['--headless', '--disable-features=A,B', '--disable-features', 'B,C'];
  assert.deepEqual(chromeArgs(args), ['--headless', '--disable-features=MacAppCodeSignClone,A,B,C']);
  assert.equal(args.length, 4);
  assert.deepEqual(chromeArgs(chromeArgs(args)), chromeArgs(args));
  assert.deepEqual(chromeArgs(), ['--disable-features=MacAppCodeSignClone']);
});

test('policy checks each launch, shared args, configs and embedded previews', () => {
  const prelude = "import { chromeArgs } from './chrome-args.mjs'; import {chromium} from 'playwright';";
  const safe = 'chromium.launch({args:chromeArgs(["--headless"])});';
  assert.deepEqual(checkChromeLaunches('a.mjs', prelude + safe), []);
  assert.equal(checkChromeLaunches('a.mjs', prelude + safe + 'chromium.launch({});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', prelude + 'chromium.launch({args:[]});').length, 1);
  assert.equal(checkChromeLaunches('a.mjs', safe).length, 1);
  assert.deepEqual(checkChromeLaunches('playwright.config.ts', prelude + 'const args=chromeArgs(); defineConfig({use:{launchOptions:{args}}});'), []);
  assert.equal(checkChromeLaunches('playwright.config.ts', "defineConfig({use:{channel:'chrome'}})").length, 1);
  assert.equal(checkChromeLaunches('a.py', 'script = r"""chromium.launchPersistentContext(profile,{channel:"chrome"});"""').length, 1);
  assert.equal(checkChromeLaunches('a.sh', 'chrome --remote-debugging-port=9222').length, 1);
});
