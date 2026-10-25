// Contract tests against the stub page: URL building, pose echo, viewport size, ID pass, projection.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { contractUrl, main } from '../render.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const golden = JSON.parse(fs.readFileSync(path.join(HERE, '..', '..', 'tests', 'golden_projection.json'), 'utf8'));

test('contractUrl encodes the pose and escapes + in time', () => {
  const v = { pose: { E: 23459676.5, N: 6704242.25, H: 5.6, yaw: 324.8, pitch: -12, roll: 0, fov: 60 }, time: '2019-06-15T14:00:00+03:00' };
  const u = new URL(contractUrl('http://h:1/', v, { pass: 'id', backend: 'webgl' }));
  assert.equal(u.searchParams.get('cam'), '23459676.5,6704242.25,5.6,324.8,-12,60');
  assert.equal(u.searchParams.get('time'), '2019-06-15T14:00:00+03:00');
  assert.ok(u.search.includes('%2B03'), u.search);
  assert.equal(u.searchParams.get('pass'), 'id');
  assert.equal(u.searchParams.get('backend'), 'webgl');
  assert.equal(u.searchParams.get('roll'), null);
});

test('render.mjs against the stub: colour + id pass, sizes, id table', async () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-render-'));
  const views = path.join(out, 'views.jsonl');
  const v = { id: 'stub_front', image: 'x.jpg', width: 640, height: 480, time: '2019-06-15T14:00:00+03:00',
    pose: { E: 23459676.0, N: 6704242.7, H: 5.6, yaw: 324.8, pitch: 12, roll: 0, fov: 60 } };
  fs.writeFileSync(views, JSON.stringify(v) + '\n');
  const failed = await main(['--views', views, '--serve', 'stub', '--out', out]);
  assert.equal(failed, 0);
  for (const f of ['stub_front.png', 'stub_front.id.png', 'stub_front.id.json', 'stub_front.meta.json', 'render-summary.json'])
    assert.ok(fs.existsSync(path.join(out, f)), f);
  const png = fs.readFileSync(path.join(out, 'stub_front.png'));
  assert.equal(png.readUInt32BE(16), 640); assert.equal(png.readUInt32BE(20), 480);
  const table = JSON.parse(fs.readFileSync(path.join(out, 'stub_front.id.json'), 'utf8'));
  assert.equal(table['1'].id, 'office_LR15');
  // centre pixel of the id pass must be the office building (id 1 -> rgb(0,0,1))
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const b64 = fs.readFileSync(path.join(out, 'stub_front.id.png')).toString('base64');
  const px = await page.evaluate(async (src) => {
    const img = new Image(); img.src = src; await img.decode();
    const c = new OffscreenCanvas(img.width, img.height); const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, img.width, img.height).data;
    const ids = new Set(); for (let i = 0; i < d.length; i += 4) ids.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
    const k = (240 * img.width + 320) * 4;
    return { centre: (d[k] << 16) | (d[k + 1] << 8) | d[k + 2], ids: [...ids].sort((a, b) => a - b) };
  }, 'data:image/png;base64,' + b64);
  await browser.close();
  assert.equal(px.centre, 1);
  for (const id of px.ids) assert.ok(id === 0 || table[String(id)], `id ${id} not in table`);
});

test('stub __qaProject matches the Python camera (golden_projection.json)', async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: golden.width, height: golden.height } });
  const page = await ctx.newPage();
  const stub = 'file://' + path.join(HERE, '..', 'stub', 'index.html');
  for (const c of golden.cases) {
    const url = contractUrl(stub, { pose: c.pose });
    await page.goto(url);
    await page.waitForFunction(() => window.__qaReady === true);
    const got = await page.evaluate(([e, n, h]) => window.__qaProject(e, n, h), c.point);
    if (c.px === null) assert.equal(got, null);
    else { assert.ok(Math.abs(got[0] - c.px[0]) < 1e-3 && Math.abs(got[1] - c.px[1]) < 1e-3, `${JSON.stringify(c)} got ${got}`); }
  }
  await browser.close();
});

test('bad cam reports __qaError and render.mjs fails the view', async () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-render-bad-'));
  const views = path.join(out, 'views.jsonl');
  fs.writeFileSync(views, JSON.stringify({ id: 'bad', width: 320, height: 240, pose: { E: 'x', N: 1, H: 1, yaw: 0, pitch: 0, roll: 0, fov: 60 } }) + '\n');
  const failed = await main(['--views', views, '--serve', 'stub', '--out', out, '--pass', 'color', '--timeout', '10000']);
  assert.equal(failed, 1);
  const meta = JSON.parse(fs.readFileSync(path.join(out, 'bad.meta.json'), 'utf8'));
  assert.match(meta.passes.color.error, /__qaError/);
});
