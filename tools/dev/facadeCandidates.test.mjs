// node --test tools/dev/facadeCandidates.test.mjs: the facade candidate viewer on a synthetic task.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { facadeCandidates, isFacadeCandidates } from './facadeCandidates.mjs';

// The smallest valid PNG (1x1, RGBA).
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

/** A task as textures/turkutex/facade_candidates.py writes it: one wall rejected twice, one accepted on retry. */
function syntheticTask(root, task = 'content-facades-synthetic') {
  const candidate = (wall, n, accepted, problems, notes, extra = {}) => ({
    n, task, facade_id: wall, building_id: wall.split('_')[0], image: `${n}.png`, sha256: `${wall}${n}`.padEnd(64, '0'),
    width_px: 1, height_px: 1, accepted, kept_at: `2026-10-27T20:3${n}:00`, audit: null,
    position: { E: 23459680, N: 6704260, spawn: [23459680, 6704252, 0], tile: 't0_0_0' },
    review: { accepted, problems, answer: { facade_id: wall, storeys: accepted ? 3 : 4, expected_storeys: 3, upper_openings: 8,
      expected_upper_openings: 8, grid_matches: accepted, orthographic: true, foreign_objects: [], clean: true, notes } },
    generation: { model: 'gpt-6-luna', effort: 'low', prompt_summary: '20.00 x 12.00 m, 1.6667:1. Three storeys.',
      image_prompt: 'An orthographic elevation <of> the wall', retry_feedback: null, ...extra },
  });
  const walls = {
    'gis-103277356L_69': [
      candidate('gis-103277356L_69', 1, false, ['window or ground-opening grid mismatch'], 'Wrong roof-dormer grid.'),
      candidate('gis-103277356L_69', 2, false, ['storey count mismatch'], 'Basement openings <missing>.', { retry_feedback: 'Wrong roof-dormer grid.' }),
    ],
    '103277350D_31': [
      candidate('103277350D_31', 1, false, ['window or ground-opening grid mismatch'], 'Ground floor openings wrong.'),
      candidate('103277350D_31', 2, true, [], 'Matches.', { retry_feedback: 'Ground floor openings wrong.' }),
    ],
  };
  for (const [wall, list] of Object.entries(walls)) {
    fs.mkdirSync(path.join(root, task, wall), { recursive: true });
    for (const c of list) {
      fs.writeFileSync(path.join(root, task, wall, c.image), PNG);
      fs.writeFileSync(path.join(root, task, wall, `${c.n}.json`), JSON.stringify(c));
    }
  }
  const every = Object.values(walls).flat();
  fs.writeFileSync(path.join(root, task, 'index.json'), JSON.stringify({
    task, updated: '2026-10-27T20:40:00',
    counts: { walls: 2, candidates: every.length, accepted: every.filter(c => c.accepted).length, rejected: every.filter(c => !c.accepted).length },
    walls: Object.entries(walls).map(([facade_id, candidates]) => ({ facade_id, building_id: candidates[0].building_id,
      position: candidates[0].position, accepted: candidates.some(c => c.accepted), candidates })),
  }));
  return task;
}

test('viewer: tasks, walls side by side with verdicts and a game link, files, no traversal', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'facade-candidates-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const task = syntheticTask(root);
  const handler = facadeCandidates({ root });
  const server = http.createServer((req, res) => Promise.resolve(handler(req, res)).catch(() => { res.writeHead(500); res.end(); }));
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (p) => fetch(base + p, { redirect: 'manual' });

  const moved = await get('/facades/candidates');
  assert.equal(moved.status, 301);
  assert.equal(moved.headers.get('location'), '/facades/candidates/');
  const list = await (await get('/facades/candidates/')).text();
  assert.match(list, new RegExp(`<a href="${task}/">${task}</a>`));
  assert.match(list, /<td class="n">4<\/td><td class="n">1<\/td><td class="n">3<\/td>/); // candidates, accepted, rejected

  assert.equal((await get(`/facades/candidates/${task}`)).status, 301);
  const r = await get(`/facades/candidates/${task}/`);
  assert.equal(r.status, 200);
  assert.match(r.headers.get('content-type'), /text\/html/);
  const html = await r.text();
  // The rejected wall comes first, with both candidates, the reviewer's reasons and the retry feedback.
  assert.ok(html.indexOf('gis-103277356L_69') < html.indexOf('103277350D_31'));
  assert.match(html, /<img src="gis-103277356L_69\/1\.png"/);
  assert.match(html, /<img src="gis-103277356L_69\/2\.png"/);
  assert.match(html, /Basement openings &lt;missing&gt;\./); // escaped
  assert.match(html, /retry after: Wrong roof-dormer grid\./);
  assert.match(html, /storey count mismatch/);
  assert.equal((html.match(/badge bad">rejected/g) ?? []).length, 4); // 3 rejected candidates + the rejected wall
  assert.equal((html.match(/badge ok">accepted/g) ?? []).length, 2);
  assert.match(html, /href="\/\?tiles=\/tiles\/tileset\.json&amp;spawn=23459680,6704252,0">open in game/);
  assert.match(html, /tile t0_0_0/);

  const png = await get(`/facades/candidates/${task}/gis-103277356L_69/2.png`);
  assert.equal(png.status, 200);
  assert.equal(png.headers.get('content-type'), 'image/png');
  assert.ok(Buffer.from(await png.arrayBuffer()).equals(PNG));
  assert.equal(JSON.parse(await (await get(`/facades/candidates/${task}/index.json`)).text()).counts.candidates, 4);
  assert.equal(JSON.parse(await (await get(`/facades/candidates/${task}/103277350D_31/2.json`)).text()).accepted, true);
  for (const bad of [`/facades/candidates/nope/`, `/facades/candidates/${task}/gis-103277356L_69/x.txt`,
    `/facades/candidates/${task}/%2e%2e/index.json`, `/facades/candidates/${task}/../${task}/index.json`,
    `/facades/candidates/${task}/a/b/1.png`, `/facades/candidates/%E0%A4%A/`]) {
    let status; // called directly: fetch would normalise the dot segments before they reach the server
    await handler({ url: bad, headers: {} }, { writeHead: (s) => { status = s; }, end() {} });
    assert.equal(status, 404, bad);
  }
  assert.ok(isFacadeCandidates('/facades/candidates/x/?a=1'));
  assert.ok(!isFacadeCandidates('/facades/candidatesx'));
});

test('viewer: an empty or missing root lists no tasks', async () => {
  const handler = facadeCandidates({ root: path.join(os.tmpdir(), 'facade-candidates-missing-root') });
  let status, body;
  handler({ url: '/facades/candidates/', headers: {} }, { writeHead: (s) => { status = s; }, end: (b) => { body = b; } });
  assert.equal(status, 200);
  assert.match(body, /No candidates kept yet/);
});
