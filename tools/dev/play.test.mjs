import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fingerprint, inspectTiles, aoi } from './play.mjs';
import { renderReport, summarize } from '../../game/tools/playtest-report.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(process.env.TURKU3D_WORK ?? os.tmpdir(), 'play-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}
test('ports and outputs stay in the assigned sandbox', () => {
  const settings = env => JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `import { settings } from './tools/dev/play.mjs'; console.log(JSON.stringify(settings()));`],
  { encoding: 'utf8', env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] }));
  const s = settings({ TURKU3D_WORK: '/tmp/my sandbox', TURKU3D_PORT_BASE: '5320' });
  assert.equal(s.port, 5320); assert.equal(s.game, '/tmp/my sandbox/build/game');
  assert.equal(s.report, '/tmp/my sandbox/reports/playtest');
  for (const value of ['garbage', '0', '65535', '1.5']) assert.throws(() => settings({ TURKU3D_PORT_BASE: value }));
});
test('cache invalidates content edits, additions and deletions, even if mtime is unchanged', t => {
  const root = fixture(t); fs.mkdirSync(path.join(root, 'src'));
  const file = path.join(root, 'src/main.ts'); fs.writeFileSync(file, 'one');
  const time = fs.statSync(file).mtime;
  const first = fingerprint(root, ['src']);
  fs.writeFileSync(file, 'two'); fs.utimesSync(file, time, time);
  assert.notEqual(fingerprint(root, ['src']), first);
  fs.writeFileSync(file, 'one'); assert.equal(fingerprint(root, ['src']), first);
  fs.writeFileSync(path.join(root, 'src/new.ts'), 'new'); assert.notEqual(fingerprint(root, ['src']), first);
  fs.unlinkSync(path.join(root, 'src/new.ts')); assert.equal(fingerprint(root, ['src']), first);
});
test('AOI reuse requires four visuals, 64 collision tiles and matching artifact hashes', t => {
  const root = fixture(t);
  assert.deepEqual(inspectTiles(root).missing, aoi);
  fs.writeFileSync(path.join(root, 'tile.bin'), 'tile');
  const entry = { url: 'tile.bin', sha256: createHash('sha256').update('tile').digest('hex') };
  const manifest = { tileset: entry, visual: Object.fromEntries(aoi.map(id => [id, entry])), collision: {} };
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) manifest.collision[`c_${i}_${j}`] = entry;
  const write = () => fs.writeFileSync(path.join(root, 'manifest.json'), JSON.stringify(manifest));
  write(); assert.deepEqual(inspectTiles(root).missing, []);
  delete manifest.collision.c_7_7; write(); assert.deepEqual(inspectTiles(root).missing, ['t0_1_1']);
  fs.writeFileSync(path.join(root, 'tile.bin'), 'corrupt'); assert.throws(() => inspectTiles(root), /corrupt tile/);
  manifest.tileset = { ...entry, url: '../outside' }; write(); assert.throws(() => inspectTiles(root), /escapes/);
});
test('standalone report embeds status, failures and relative video links without injecting HTML', () => {
  const report = { status: 'failed', duration: 1200, date: 'today', commit: 'abc', dirty: false, selection: ['/@ground/'],
    gaps: ['Missing quay'], rows: [{ title: '<script>bad</script>', backend: 'webgpu', tags: ['@ground'], status: 'failed', duration: 100,
      errors: ['x < 1'], evidence: null, attachments: [{ type: 'video/webm', name: 'video', url: 'media/a.webm' }] }] };
  const html = renderReport(report);
  assert.match(html, /Real-AOI follow-up/); assert.match(html, /Missing quay/);
  assert.match(html, /video controls preload="metadata" src="media\/a.webm"/);
  assert.match(html, /&lt;script&gt;/); assert.ok(!html.includes('<script>bad'));
  assert.match(html, /x &lt; 1/); assert.match(html, /Suite: <strong>FAILED/);
});
test('report measurements retain a transient height drop and peak speed', () => {
  assert.equal(summarize([]), null);
  const stats = summarize([{ feet: [0, 2, 0], frameMs: 16, speed: 1, ground: 2 },
    { feet: [0, -8, 0], frameMs: 40, speed: 9, ground: null }, { feet: [0, 2, 0], frameMs: 20, speed: 0, ground: 2 }]);
  assert.equal(stats.minH, -8); assert.equal(stats.maxSpeed, 9); assert.equal(stats.p95, 40); assert.equal(stats.groundChecks, 2);
});
