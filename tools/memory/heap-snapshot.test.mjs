import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { analyze, readSnapshot, summarizeFile, take } from './heap-snapshot.mjs';

const META = {
  node_fields: ['type', 'name', 'id', 'self_size', 'edge_count', 'trace_node_id', 'detachedness'],
  node_types: [['hidden', 'array', 'string', 'object', 'code', 'closure', 'regexp', 'number', 'native', 'synthetic',
    'concatenated string', 'sliced string', 'symbol', 'bigint', 'object shape'], 'string', 'number', 'number', 'number', 'number', 'number'],
  edge_fields: ['type', 'name_or_index', 'to_node'],
  edge_types: [['context', 'element', 'property', 'internal', 'hidden', 'shortcut', 'weak'], 'string_or_number', 'node'],
};

/** A small snapshot: the window holds two Holders sharing one Big (dominated by the window), a nested Holder with a
 *  typed array, a string with escapes and an object only weakly held. */
function synthetic() {
  const strings = ['', 'Window', 'Holder', 'Big', 'Float32Array', 'ArrayBuffer', 'system / JSArrayBufferData', 'Weakling',
    'a', 'b', 'shared', 'own', 'array', 'buffer', 'backing_store', 'w', 's', 'quoted "x" é \\ long text tail'];
  const T = Object.fromEntries(META.node_types[0].map((t, i) => [t, i])), E = Object.fromEntries(META.edge_types[0].map((t, i) => [t, i]));
  const S = name => strings.indexOf(name);
  const nodes = [
    [T.synthetic, 0, 1, 0, [[E.shortcut, 1, 1]]],
    [T.object, S('Window'), 3, 10, [[E.property, S('a'), 2], [E.property, S('b'), 3], [E.weak, S('w'), 6], [E.property, S('s'), 10]]],
    [T.object, S('Holder'), 5, 100, [[E.property, S('shared'), 4], [E.property, S('own'), 5]]],
    [T.object, S('Holder'), 7, 50, [[E.property, S('shared'), 4]]],
    [T.object, S('Big'), 9, 1000, []],
    [T.object, S('Holder'), 11, 7, [[E.property, S('array'), 7]]],
    [T.object, S('Weakling'), 13, 500, []],
    [T.object, S('Float32Array'), 15, 20, [[E.internal, S('buffer'), 8]]],
    [T.object, S('ArrayBuffer'), 17, 30, [[E.internal, S('backing_store'), 9]]],
    [T.native, S('system / JSArrayBufferData'), 19, 4096, []],
    [T.string, strings.length - 1, 21, 64, []],
  ];
  const nodeRows = [], edgeRows = [];
  for (const [type, name, id, size, edges] of nodes) {
    nodeRows.push(type, name, id, size, edges.length, 0, 0);
    for (const [t, n, to] of edges) edgeRows.push(t, n, to * META.node_fields.length);
  }
  return JSON.stringify({ snapshot: { meta: META, node_count: nodes.length, edge_count: edgeRows.length / 3, trace_function_count: 0 },
    nodes: nodeRows, edges: edgeRows, trace_function_infos: [], trace_tree: [], samples: [], locations: [], strings });
}

async function* pieces(text, size) { for (let i = 0; i < text.length; i += size) yield text.slice(i, i + size); }

test('parses a snapshot fed in tiny chunks and cuts long strings without breaking escapes', async () => {
  const text = synthetic();
  for (const size of [1, 3, 7, 64, text.length]) {
    const snap = await readSnapshot(pieces(text, size), { stringCap: 12 });
    assert.equal(snap.nodes.length, 11 * 7);
    assert.equal(snap.strings.length, 18);
    assert.equal(snap.strings[6], 'system / JSA'); // names longer than the cap become previews
    assert.equal(snap.strings[17], 'quoted "x"'); // the cap counts JSON characters; escapes stay whole
  }
});

test('retained sizes follow the dominator tree: shared objects go to the common owner, weak edges retain nothing', async () => {
  const a = analyze(await readSnapshot(pieces(synthetic(), 5)), { minMiB: 0 });
  const cls = name => a.classesByRetained.find(c => c.name === name);
  assert.equal(cls('Holder').retained, 100 + 7 + 20 + 30 + 4096 + 50); // the nested Holder is not counted twice
  assert.equal(cls('Holder').count, 3);
  assert.equal(cls('Window').retained, 10 + 100 + 50 + 1000 + 7 + 20 + 30 + 4096 + 64); // Big: both Holders hold it
  assert.equal(cls('Big').retained, 1000);
  assert.equal(a.reachable, 10); // the weakly held object is unreachable
  assert.equal(a.totalSelf - a.reachableSelf, 500);
  assert.deepEqual(a.buffers.paths.map(p => [p.path, p.bytes]), [['a.own.array', 4096]]);
  const big = a.drill.find(d => d.name === 'Big');
  assert.equal(big.via, 'Holder.shared');
});

test('summarizes a real V8 snapshot: a 16 MiB typed array in a Map shows under its owner and property path', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'heap-test-')), file = path.join(dir, 'probe.heapsnapshot');
  try {
    execFileSync(process.execPath, ['-e', `class GroundIndexProbe { constructor() { this.cells = new Map([['c', new Float32Array(4 << 20)]]); } }
      globalThis.probe = new GroundIndexProbe(); require('v8').writeHeapSnapshot(process.argv[1]);`, file]);
    const { analysis, text } = await summarizeFile(file, { 'min-mib': 8 });
    const owner = analysis.classesByRetained.find(c => c.name === 'GroundIndexProbe');
    assert.ok(owner && owner.retained >= 16 * 2 ** 20, text);
    const held = analysis.buffers.paths.find(p => p.path.includes('cells'));
    assert.ok(held && held.bytes >= 16 * 2 ** 20, text);
    assert.ok(analysis.drill.some(d => d.name === 'GroundIndexProbe'), text);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('take waits for the replay, records the census and streams a gzip snapshot (Node inspector stands in for the page)', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'heap-take-'));
  const child = spawn(process.execPath, ['--inspect=127.0.0.1:0', '-e', `globalThis.__bench = { phase: 'play', done: false };
    setTimeout(() => { globalThis.__bench = { phase: 'done', done: true }; }, 2500);
    globalThis.__render = { memory: async () => ({ rows: [{ name: 'probe', bytes: 1 }] }) }; setInterval(() => {}, 1000);`], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    const ws = await new Promise((resolve, reject) => {
      let err = '';
      child.stderr.on('data', d => { err += d; const m = /ws:\/\/\S+/.exec(err); if (m) resolve(m[0]); });
      child.on('exit', () => reject(new Error(err)));
    });
    const lines = [];
    await take({ ws, out: dir, log: line => lines.push(line) });
    assert.ok(lines.some(l => l.includes('play')) && lines.some(l => l.includes('done')), lines.join('\n'));
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'census.json'), 'utf8')).rows[0].name, 'probe');
    assert.ok(JSON.parse(fs.readFileSync(path.join(dir, 'usage.json'), 'utf8')).page.usedSize > 0);
    assert.ok(fs.statSync(path.join(dir, 'page.heapsnapshot.gz')).size > 10000);
    assert.match(fs.readFileSync(path.join(dir, 'summary.txt'), 'utf8'), /classes by retained size/);
  } finally { child.kill(); fs.rmSync(dir, { recursive: true, force: true }); }
});
