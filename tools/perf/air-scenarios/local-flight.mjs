// Exercise the identical flight on this Mac while exclusive Air access is unavailable.
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser } from '../../dev/environment.mjs';
isolateBrowser();
const [base, cdpPort, output] = process.argv.slice(2);
if (!output) throw new Error('Usage: local-flight.mjs GAME_URL OWN_CDP_PORT OUTPUT');
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true,
  ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', `--remote-debugging-port=${Number(cdpPort)}`]) });
try {
  const page = await browser.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  const child = spawn(process.execPath, [new URL('./run.mjs', import.meta.url).pathname,
    `http://127.0.0.1:${Number(cdpPort)}`, base, output, 'flight'], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  if (code !== 0) throw new Error(`Local flight exited ${code}`);
} finally { await browser.close(); }
