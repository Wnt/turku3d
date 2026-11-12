import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CATEGORIES, aggregate, buildItems, reportHtml, reportText, validateScores } from './scene-judge.mjs';

const view = (id, photo) => ({ id, file: `current/${id}.png`, ...(photo ? { photo: { id: photo, image: `data/qa/refs/${photo}.jpg`, date: '2019-06', poseConfidence: 'medium' } } : {}) });
const captures = [
  { dir: '/c/gameplay', manifest: { mode: 'render', views: [view('office-frontage'), view('quay')] } },
  { dir: '/c/refs', manifest: { mode: 'render', views: [view('sv-02', 'sv_02'), view('boat-01', 'boat_01')] } },
];
const answer = (items, score) => ({ summary: 's', views: items.map(i => ({ id: i.id, note: 'n', issues: [],
  scores: Object.fromEntries(CATEGORIES.map(c => [c, score(i, c)])) })) });

test('packet items are blind: posed views first, neutral ids, the key keeps the capture names', () => {
  const { items, key } = buildItems(captures);
  assert.deepEqual(items.map(i => [i.id, i.kind]), [['p01', 'posed'], ['p02', 'posed'], ['g01', 'gameplay'], ['g02', 'gameplay']]);
  assert.ok(!JSON.stringify(items).match(/sv_02|office|\/c\//), 'nothing in the packet names a view, capture or photo source');
  assert.equal(key[0].view, 'sv-02');
  assert.throws(() => buildItems([{ dir: '/x', manifest: { failure: 'Error: boom', views: [] } }]), /capture failed/);
  assert.throws(() => buildItems([{ dir: '/x', manifest: { mode: 'play', views: [] } }]), /scene alone/);
});

test('judge answers must cover the packet exactly, with integer scores 0–10', () => {
  const { items } = buildItems(captures);
  assert.doesNotThrow(() => validateScores(answer(items, () => 5), items));
  assert.throws(() => validateScores(answer(items.slice(1), () => 5), items), /do not match/);
  assert.throws(() => validateScores(answer(items, () => 5.5), items), /not an integer/);
  assert.throws(() => validateScores(answer(items, () => 11), items), /not an integer/);
});

test('aggregation: repeats averaged, weighted overall 0–100, spread and per-kind scores', () => {
  const { items } = buildItems(captures);
  const a = answer(items, (i, c) => c === 'identity' ? 6 : 4), b = answer(items, (i, c) => c === 'identity' ? 8 : 4);
  const result = aggregate([a, b], items);
  assert.equal(result.means.identity, 7);
  assert.equal(result.means.street, 4);
  assert.equal(result.overall, 49); // 0.3 * 7 + 0.7 * 4 = 4.9
  assert.deepEqual(result.repeats, [46, 52]);
  assert.equal(result.spread, 6);
  assert.deepEqual(Object.keys(result.byKind), ['posed', 'gameplay']);
});

test('the report shows each iteration and its change from the previous one', () => {
  const { items } = buildItems(captures);
  const row = (iteration, score) => ({ iteration, at: '2026-11-12T12:00:00Z', label: `it ${iteration}`, git: { sha: 'abcdef12', dirty: false },
    captures: [], ...aggregate([answer(items, () => score)], items) });
  const rows = [row(1, 4), row(2, 6)];
  const text = reportText(rows);
  assert.match(text, /it 2 +60\.0 +0\.0 +\+20\.0/);
  assert.match(reportHtml(rows, 'Visual judge'), /<polyline/);
});

test('a subset view set: only its views of a capture are judged, and a finished run is rescored without judging again', async () => {
  const fs = await import('node:fs'), os = await import('node:os'), path = await import('node:path');
  const { record, groupOf, groupText } = await import('./scene-judge.mjs');
  const pilot = [{ dir: '/c/pilot', manifest: { mode: 'render', viewsFile: 'scripts/dev/views/pilot-locations.json',
    views: [view('portsa-01', 'portsa_01'), view('portsa-02', 'portsa_02'), view('nummi-01', 'nummi_01'), { ...view('dev-01', 'dev_01'), group: 'houses' }] } }];
  const { items, key } = buildItems(pilot, new Set(['portsa-02', 'nummi-01']));
  assert.deepEqual(key.map(k => [k.id, k.view, k.group]), [['p01', 'portsa-02', 'portsa'], ['p02', 'nummi-01', 'nummi']]);
  assert.equal(items.length, 2);
  assert.throws(() => buildItems(pilot, new Set(['portsa-02', 'rettig-01'])), /lack the views rettig-01/);
  assert.equal(groupOf(pilot[0].manifest.views[3]), 'houses');
  assert.equal(groupOf({ id: 'jokiranta-sv-05' }), 'jokiranta');

  // A full run of four views, then its two Portsa views recorded into their own history.
  const all = buildItems(pilot), out = fs.mkdtempSync(path.join(os.tmpdir(), 'judge-subset-'));
  fs.mkdirSync(path.join(out, 'scores'));
  fs.writeFileSync(path.join(out, 'key.json'), JSON.stringify({ items: all.items, key: all.key, captures: [] }));
  const byView = { 'portsa-01': 2, 'portsa-02': 4, 'nummi-01': 8, 'dev-01': 6 };
  for (const [n, plus] of [[1, 0], [2, 2]]) fs.writeFileSync(path.join(out, 'scores', `judge-${n}.json`),
    JSON.stringify(answer(all.items, i => byView[all.key.find(k => k.id === i.id).view] + plus)));
  const full = record(out, 'full', path.join(out, 'full.jsonl'));
  assert.equal(full.overall, 60); // views 3, 5, 9, 7 after both repeats
  assert.deepEqual(Object.fromEntries(Object.entries(full.byGroup).map(([g, v]) => [g, v.overall])), { portsa: 40, nummi: 90, houses: 70 });
  const subset = record(out, 'portsa', path.join(out, 'portsa.jsonl'), new Set(['portsa-01', 'portsa-02']));
  assert.equal(subset.overall, 40);
  assert.deepEqual(subset.repeats, [30, 50]);
  assert.equal(subset.judgedWith, 4);
  assert.deepEqual(subset.views.map(v => v.view), ['portsa-01', 'portsa-02']);
  assert.match(groupText([full]), /nummi +1 +90\.0/);
  assert.throws(() => record(out, 'x', path.join(out, 'x.jsonl'), new Set(['rettig-01'])), /did not judge the views rettig-01/);
  fs.rmSync(out, { recursive: true, force: true });
});

test('a large set is judged in equal packets, and views carry their aim', async () => {
  const { packetsOf } = await import('./scene-judge.mjs');
  const many = n => Array.from({ length: n }, (_, i) => ({ id: `p${i}` }));
  assert.deepEqual(packetsOf(many(24), 24).map(p => p.length), [24]);
  assert.deepEqual(packetsOf(many(48), 24).map(p => p.length), [24, 24]);
  assert.deepEqual(packetsOf(many(30), 24).map(p => p.length), [15, 15]);
  const set = [{ dir: '/c/dev', manifest: { mode: 'render', views: [{ ...view('dev-01-b', 'dev_01'), group: 'houses', aim: 'building' },
    { ...view('dev-01-s', 'dev_01_street'), group: 'houses', aim: 'street' }] } }];
  const { items, key } = buildItems(set);
  const result = aggregate([answer(items, i => i.id === 'p01' ? 2 : 6), answer(items, i => i.id === 'p01' ? 4 : 6)], items, key.map(k => k.group), key.map(k => k.aim));
  assert.deepEqual(Object.fromEntries(Object.entries(result.byAim).map(([k, v]) => [k, [v.overall, v.spread]])), { building: [30, 20], street: [60, 0] });
  assert.deepEqual(Object.keys(result.byGroup), ['houses']);
});
