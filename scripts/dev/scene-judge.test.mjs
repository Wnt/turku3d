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
