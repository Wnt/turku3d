#!/usr/bin/env node
// Heap snapshot of the game page at the end pose of a replay, and a summary of what holds the memory: retained
// sizes over the dominator tree by class, a drill-down of the largest owners, ArrayBuffer bytes by the property path
// that holds them (survives minified class names) and duplicate strings. Also records `__render.memory()` and the
// V8 heap usage of the page and each worker at the same moment.
//
//   # Chrome started with --remote-debugging-port=9222, a drive replay open (?bench=drive&capture=ID&segment=N):
//   node tools/memory/heap-snapshot.mjs take --cdp http://127.0.0.1:9222 --url bench=drive --out /tmp/heap-end
//   # or let it open the replay itself:
//   node tools/memory/heap-snapshot.mjs take --cdp http://127.0.0.1:9222 --open 'http://localhost:5241/?bench=drive&capture=ID&segment=1'
//   node tools/memory/heap-snapshot.mjs summarize /tmp/heap-end/page.heapsnapshot.gz [--top 30] [--min-mib 4]
//
// take waits for `__bench.done` (`--wait now` snapshots at once), forces a GC, streams the snapshot gzip-compressed to
// OUT/page.heapsnapshot.gz (gunzip it to open in DevTools), writes OUT/census.json, OUT/usage.json and
// OUT/summary.txt. `--workers` also snapshots each worker (OUT/worker-N.heapsnapshot.gz); `--no-summary` leaves the
// summary for another machine (it needs about 60 bytes per node plus 8 per edge, ~1-2 GB for a 1 GB heap). Taking
// the snapshot needs extra renderer memory of the same order as the heap: on an 8 GB machine close other tabs first.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { finished } from 'node:stream/promises';
import { pathToFileURL } from 'node:url';

const MiB = 2 ** 20;
const NONE = 0xffffffff;

class Cdp {
  constructor(url) { this.url = url; this.seq = 0; this.pending = new Map(); this.listeners = new Set(); }
  async open() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP connection timed out')), 15000);
      this.socket.onopen = () => { clearTimeout(timer); resolve(); };
      this.socket.onerror = error => { clearTimeout(timer); reject(new Error(`CDP connection failed: ${error.message ?? error.type}`)); };
    });
    this.socket.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      if (msg.id && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id); this.pending.delete(msg.id); clearTimeout(p.timer);
        msg.error ? p.reject(new Error(`${p.method}: ${JSON.stringify(msg.error)}`)) : p.resolve(msg.result);
      } else if (msg.method) for (const fn of this.listeners) fn(msg);
    };
  }
  send(method, params = {}, sessionId, timeoutMs = 120000) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  close() { this.socket?.close(); }
}

function parseArgs(argv) {
  const opts = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { opts._.push(a); continue; }
    const key = a.slice(2);
    if (['workers', 'no-summary'].includes(key)) opts[key] = true;
    else opts[key] = argv[++i];
  }
  return opts;
}

async function pageSocket(opts) {
  const base = (opts.cdp ?? 'http://127.0.0.1:9222').replace(/\/$/, '');
  if (opts.open) {
    const target = await fetch(`${base}/json/new?${encodeURI(opts.open)}`, { method: 'PUT', signal: AbortSignal.timeout(10000) }).then(r => r.json());
    return { ws: target.webSocketDebuggerUrl, url: opts.open };
  }
  const pages = (await fetch(`${base}/json/list`, { signal: AbortSignal.timeout(10000) }).then(r => r.json())).filter(t => t.type === 'page');
  const match = pages.filter(t => t.url.includes(opts.url ?? 'bench=drive'));
  if (match.length !== 1) throw new Error(`${match.length} pages match ${JSON.stringify(opts.url ?? 'bench=drive')}: ${pages.map(t => t.url).join(', ') || 'none open'}`);
  return { ws: match[0].webSocketDebuggerUrl, url: match[0].url };
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Snapshot one isolate (the page, or a worker session) into `file`, gzip-compressed. */
async function snapshotTo(cdp, file, sessionId) {
  const gz = zlib.createGzip({ level: 1 }), out = fs.createWriteStream(file);
  gz.pipe(out);
  const onChunk = msg => { if (msg.method === 'HeapProfiler.addHeapSnapshotChunk' && msg.sessionId === sessionId) gz.write(msg.params.chunk); };
  cdp.listeners.add(onChunk);
  try {
    await cdp.send('HeapProfiler.enable', {}, sessionId);
    await cdp.send('HeapProfiler.collectGarbage', {}, sessionId, 300000);
    await cdp.send('HeapProfiler.takeHeapSnapshot', { reportProgress: false, captureNumericValue: false }, sessionId, 1800000);
  } finally {
    cdp.listeners.delete(onChunk);
    gz.end();
    await finished(out);
  }
  return fs.statSync(file).size;
}

export async function take(opts) {
  const direct = !!opts.ws;
  const { ws, url } = direct ? { ws: opts.ws, url: opts.ws } : await pageSocket(opts);
  const out = opts.out ?? fs.mkdtempSync(path.join(os.tmpdir(), 'heap-'));
  fs.mkdirSync(out, { recursive: true });
  const cdp = new Cdp(ws);
  await cdp.open();
  const log = opts.log ?? (line => console.log(line));
  try {
    const evaluate = async (expression, timeoutMs) => {
      const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, undefined, timeoutMs);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
      return r.result.value;
    };
    const status = () => evaluate(`(() => { const b = globalThis.__bench; return b ? JSON.stringify({ phase: b.phase, done: !!b.done,
      error: b.error ?? null, progress: b.progress ?? '' }) : null; })()`).then(s => s && JSON.parse(s));
    if ((opts.wait ?? 'done') !== 'now') {
      const deadline = Date.now() + Number(opts.timeout ?? 1800) * 1000;
      let last = '';
      for (;;) {
        const s = await status().catch(() => null);
        const line = s ? `${s.phase}${s.progress ? ` ${s.progress}` : ''}` : 'no __bench yet';
        if (line !== last) { log(`[heap] ${line}`); last = line; }
        if (s?.error) throw new Error(`replay failed: ${s.error}`);
        if (s?.done) break;
        if (Date.now() > deadline) throw new Error(`timed out waiting for __bench.done (last: ${last})`);
        await sleep(2000);
      }
    }
    const bench = await status().catch(() => null);
    const census = await evaluate(`(async () => globalThis.__render?.memory ? JSON.stringify(await globalThis.__render.memory()) : null)()`, 300000)
      .catch(error => JSON.stringify({ error: String(error) }));
    if (census) fs.writeFileSync(path.join(out, 'census.json'), census);
    const usage = { at: new Date().toISOString(), url, bench, page: await cdp.send('Runtime.getHeapUsage'), workers: [] };
    const workers = [];
    if (!direct) {
      // Dedicated workers (and the workers they start) attach as child sessions of the page.
      const attach = sessionId => cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true }, sessionId).catch(() => {});
      cdp.listeners.add(msg => {
        if (msg.method !== 'Target.attachedToTarget') return;
        const { sessionId, targetInfo } = msg.params;
        if (!/worker/.test(targetInfo.type)) return;
        workers.push({ sessionId, type: targetInfo.type, url: targetInfo.url });
        void attach(sessionId);
      });
      await attach();
      await sleep(1000);
      for (const w of workers) {
        const heap = await cdp.send('Runtime.getHeapUsage', {}, w.sessionId, 10000).catch(error => ({ error: String(error) }));
        usage.workers.push({ type: w.type, url: w.url, ...heap });
      }
    }
    const mib = v => typeof v === 'number' ? (v / MiB).toFixed(1) : '-';
    log(`[heap] page heap used ${mib(usage.page.usedSize)} of ${mib(usage.page.totalSize)} MiB, ArrayBuffer backing ${mib(usage.page.backingStorageSize)} MiB, embedder ${mib(usage.page.embedderHeapUsedSize)} MiB`);
    for (const w of usage.workers) log(`[heap]   worker ${w.url.split('/').pop()}: used ${mib(w.usedSize)}, backing ${mib(w.backingStorageSize)} MiB`);
    fs.writeFileSync(path.join(out, 'usage.json'), JSON.stringify(usage, null, 2));
    const file = path.join(out, 'page.heapsnapshot.gz');
    log(`[heap] taking the page snapshot`);
    const size = await snapshotTo(cdp, file);
    log(`[heap] ${file} (${mib(size)} MiB gzip)`);
    if (opts.workers) for (const [i, w] of workers.entries()) {
      const wf = path.join(out, `worker-${i}.heapsnapshot.gz`);
      await snapshotTo(cdp, wf, w.sessionId).catch(error => log(`[heap] worker ${w.url}: ${error.message}`));
    }
    if (!opts['no-summary']) {
      const summary = await summarizeFile(file, opts);
      fs.writeFileSync(path.join(out, 'summary.txt'), summary.text);
      fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary.json, null, 1));
      log(summary.text);
    }
    return { out, file };
  } finally { cdp.close(); }
}

/** Parse a .heapsnapshot from string chunks without building the whole JSON (snapshots exceed V8's string limit).
 *  Strings are cut to `stringCap` characters: names stay whole, long string contents become previews. */
export async function readSnapshot(chunks, { stringCap = 160 } = {}) {
  let phase = 'head', head = '', scan = '', meta = null, nodes = null, edges = null, target = null, k = 0, cur = 0, inNum = false;
  const strings = [];
  let inStr = false, esc = 0, hex = 0, seq = '', raw = '';
  const seek = { edges: '"edges":[', strings: '"strings":[' };
  const keep = text => { if (raw.length < stringCap) raw += text.length + raw.length > stringCap ? text.slice(0, stringCap - raw.length) : text; };
  const atom = escape => { if (raw.length + escape.length <= stringCap) raw += escape; }; // never cut an escape
  const feed = s => {
    let i = 0;
    while (i < s.length) {
      if (phase === 'head') {
        head += s.slice(i);
        const at = head.indexOf('"nodes":[');
        if (at < 0) return;
        meta = JSON.parse(`${head.slice(0, at).replace(/,\s*$/, '')}}`).snapshot;
        nodes = new Uint32Array(meta.node_count * meta.meta.node_fields.length);
        target = nodes; k = 0; phase = 'nums';
        s = head.slice(at + 9); i = 0; head = '';
        continue;
      }
      if (phase === 'edges' || phase === 'strings') {
        scan += s.slice(i);
        const at = scan.indexOf(seek[phase]);
        if (at < 0) { scan = scan.slice(-16); return; }
        s = scan.slice(at + seek[phase].length); i = 0; scan = '';
        if (phase === 'edges') { edges = new Uint32Array(meta.edge_count * meta.meta.edge_fields.length); target = edges; k = 0; phase = 'nums'; }
        else phase = 'str';
        continue;
      }
      if (phase === 'nums') {
        for (; i < s.length; i++) {
          const c = s.charCodeAt(i);
          if (c >= 48 && c <= 57) { cur = cur * 10 + c - 48; inNum = true; continue; }
          if (inNum) { target[k++] = cur; cur = 0; inNum = false; }
          if (c === 93) {
            if (k !== target.length) throw new Error(`snapshot ${target === nodes ? 'nodes' : 'edges'}: ${k} values, expected ${target.length}`);
            phase = target === nodes ? 'edges' : 'strings'; i++;
            break;
          }
        }
        continue;
      }
      if (phase === 'str') {
        let nextB = -1;
        for (; i < s.length; i++) {
          if (!inStr) {
            const c = s.charCodeAt(i);
            if (c === 34) { inStr = true; raw = ''; } else if (c === 93) { phase = 'done'; return; }
            continue;
          }
          if (esc === 1) { seq += s[i]; if (s[i] === 'u') { esc = 2; hex = 4; } else { atom(seq); esc = 0; } continue; }
          if (esc === 2) { seq += s[i]; if (--hex === 0) { atom(seq); esc = 0; } continue; }
          // The next backslash is searched once per chunk position, not per string (most strings have none).
          if (nextB < i) { nextB = s.indexOf('\\', i); if (nextB < 0) nextB = s.length; }
          let q = s.indexOf('"', i);
          if (q < 0) q = s.length;
          const j = Math.min(q, nextB), b = nextB;
          if (j > i) { keep(s.slice(i, j)); i = j - 1; continue; }
          if (j === b) { esc = 1; seq = '\\'; continue; }
          strings.push(JSON.parse(`"${raw}"`)); inStr = false;
        }
        return;
      }
      return;
    }
  };
  for await (const chunk of chunks) feed(typeof chunk === 'string' ? chunk : chunk.toString('utf8'));
  if (phase !== 'done') throw new Error(`truncated heap snapshot (stopped in ${phase})`);
  return { meta, nodes, edges, strings };
}

/** Dominator tree retained sizes and the views of `summarize` (Cooper-Harvey-Kennedy over the post order of the
 *  essential edges, as DevTools: weak edges never retain, shortcut edges only from the root). */
export function analyze({ meta, nodes, edges, strings }, { top = 30, minMiB = 4 } = {}) {
  const m = meta.meta, NF = m.node_fields.length, EF = m.edge_fields.length;
  const nType = m.node_fields.indexOf('type'), nName = m.node_fields.indexOf('name'), nSelf = m.node_fields.indexOf('self_size'),
    nEdges = m.node_fields.indexOf('edge_count');
  const eType = m.edge_fields.indexOf('type'), eName = m.edge_fields.indexOf('name_or_index'), eTo = m.edge_fields.indexOf('to_node');
  const nodeTypes = m.node_types[nType], edgeTypes = m.edge_types[eType];
  const N = nodes.length / NF, E = edges.length / EF, ROOT = 0;
  const WEAK = edgeTypes.indexOf('weak'), SHORTCUT = edgeTypes.indexOf('shortcut');
  const ELEMENT = edgeTypes.indexOf('element'), HIDDEN = edgeTypes.indexOf('hidden'), PROPERTY = edgeTypes.indexOf('property'),
    CONTEXT = edgeTypes.indexOf('context'), INTERNAL = edgeTypes.indexOf('internal');
  const firstEdge = new Uint32Array(N + 1);
  for (let n = 0, e = 0; n < N; n++) { firstEdge[n] = e; e += nodes[n * NF + nEdges]; firstEdge[n + 1] = e; }
  const essential = (from, type) => type !== WEAK && (type !== SHORTCUT || from === ROOT);
  const self = n => nodes[n * NF + nSelf], typeOf = n => nodeTypes[nodes[n * NF + nType]], nameOf = n => strings[nodes[n * NF + nName]] ?? '';
  const edgeName = e => {
    const t = edges[e * EF + eType], v = edges[e * EF + eName];
    return t === ELEMENT || t === HIDDEN ? `[${v}]` : String(strings[v] ?? v);
  };
  // Retainers (essential edges only), as CSR.
  const retStart = new Uint32Array(N + 1);
  for (let n = 0; n < N; n++) for (let e = firstEdge[n]; e < firstEdge[n + 1]; e++)
    if (essential(n, edges[e * EF + eType])) retStart[edges[e * EF + eTo] / NF + 1]++;
  for (let n = 0; n < N; n++) retStart[n + 1] += retStart[n];
  const retEdge = new Uint32Array(retStart[N]), retFrom = new Uint32Array(retStart[N]), fill = retStart.slice(0, N);
  for (let n = 0; n < N; n++) for (let e = firstEdge[n]; e < firstEdge[n + 1]; e++) {
    if (!essential(n, edges[e * EF + eType])) continue;
    const to = edges[e * EF + eTo] / NF, at = fill[to]++;
    retFrom[at] = n; retEdge[at] = e;
  }
  // Post order and BFS distances from the root.
  const po = new Uint32Array(N).fill(NONE), order = new Uint32Array(N), stack = new Uint32Array(N), cursor = new Uint32Array(N);
  const seen = new Uint8Array(N);
  let count = 0, sp = 0;
  stack[0] = ROOT; cursor[0] = firstEdge[ROOT]; seen[ROOT] = 1;
  while (sp >= 0) {
    const n = stack[sp], e = cursor[sp];
    if (e < firstEdge[n + 1]) {
      cursor[sp]++;
      const to = edges[e * EF + eTo] / NF;
      if (!seen[to] && essential(n, edges[e * EF + eType])) { seen[to] = 1; sp++; stack[sp] = to; cursor[sp] = firstEdge[to]; }
    } else { po[n] = count; order[count++] = n; sp--; }
  }
  const dist = new Uint32Array(N).fill(NONE);
  { let head = 0, tail = 0; stack[tail++] = ROOT; dist[ROOT] = 0;
    while (head < tail) { const n = stack[head++];
      for (let e = firstEdge[n]; e < firstEdge[n + 1]; e++) {
        const to = edges[e * EF + eTo] / NF;
        if (dist[to] === NONE && essential(n, edges[e * EF + eType])) { dist[to] = dist[n] + 1; stack[tail++] = to; }
      } } }
  // Immediate dominators, in post-order numbers (the root has the highest).
  const R = count - 1, dom = new Uint32Array(count).fill(NONE);
  dom[R] = R;
  for (let changed = true, passes = 0; changed && passes < 100; passes++) {
    changed = false;
    for (let p = R - 1; p >= 0; p--) {
      const n = order[p];
      let idom = NONE;
      for (let r = retStart[n]; r < retStart[n + 1]; r++) {
        let a = po[retFrom[r]];
        if (a === NONE || dom[a] === NONE) continue;
        if (idom === NONE) { idom = a; continue; }
        let b = idom;
        while (a !== b) { while (a < b) a = dom[a]; while (b < a) b = dom[b]; }
        idom = a;
      }
      if (idom !== NONE && dom[p] !== idom) { dom[p] = idom; changed = true; }
    }
  }
  const retained = new Float64Array(count);
  for (let p = 0; p < count; p++) retained[p] = self(order[p]);
  for (let p = 0; p < R; p++) if (dom[p] !== NONE) retained[dom[p]] += retained[p];
  // Classes as DevTools groups them.
  const classOf = n => {
    const t = typeOf(n);
    if (t === 'object' || t === 'native' || t === 'synthetic') return nameOf(n).split(' / ')[0].slice(0, 80) || `(${t})`;
    if (t === 'concatenated string' || t === 'sliced string') return '(string)';
    if (t === 'hidden') return '(system)';
    if (t === 'code') return '(compiled code)';
    return `(${t})`;
  };
  const classIds = new Map(), classNames = [], classIdx = new Uint32Array(count);
  for (let p = 0; p < count; p++) {
    const c = classOf(order[p]);
    let id = classIds.get(c);
    if (id === undefined) { id = classNames.length; classIds.set(c, id); classNames.push(c); }
    classIdx[p] = id;
  }
  const C = classNames.length, cSelf = new Float64Array(C), cCount = new Uint32Array(C), cRetained = new Float64Array(C), active = new Uint32Array(C);
  for (let p = 0; p < count; p++) { cSelf[classIdx[p]] += self(order[p]); cCount[classIdx[p]]++; }
  // Dominator tree children, then a DFS that counts each class's outermost instances only.
  const kidStart = new Uint32Array(count + 1);
  for (let p = 0; p < R; p++) if (dom[p] !== NONE) kidStart[dom[p] + 1]++;
  for (let p = 0; p < count; p++) kidStart[p + 1] += kidStart[p];
  const kids = new Uint32Array(kidStart[count]), kidFill = kidStart.slice(0, count);
  for (let p = 0; p < R; p++) if (dom[p] !== NONE) kids[kidFill[dom[p]]++] = p;
  sp = 0; stack[0] = R; cursor[0] = kidStart[R];
  if (active[classIdx[R]]++ === 0) cRetained[classIdx[R]] += retained[R];
  while (sp >= 0) {
    const p = stack[sp];
    if (cursor[sp] < kidStart[p + 1]) {
      const q = kids[cursor[sp]++];
      if (active[classIdx[q]]++ === 0) cRetained[classIdx[q]] += retained[q];
      sp++; stack[sp] = q; cursor[sp] = kidStart[q];
    } else { active[classIdx[p]]--; sp--; }
  }
  // The shortest retainer of a node (BFS order), preferring named edges.
  const via = n => {
    let best = NONE, bestEdge = NONE;
    for (let r = retStart[n]; r < retStart[n + 1]; r++) {
      const f = retFrom[r];
      if (dist[f] === NONE || dist[f] + 1 !== dist[n]) continue;
      const t = edges[retEdge[r] * EF + eType];
      if (best === NONE || (t === PROPERTY || t === CONTEXT) && edges[bestEdge * EF + eType] !== PROPERTY) { best = f; bestEdge = retEdge[r]; }
    }
    return best === NONE ? null : { from: best, edge: bestEdge };
  };
  const pathOf = (n, hops = 14) => {
    const parts = [];
    for (let h = 0; h < hops && n !== ROOT; h++) {
      const v = via(n);
      if (!v) break;
      parts.push(edgeName(v.edge));
      if (v.from === ROOT) break;
      n = v.from;
    }
    return parts.reverse().join('.').replace(/\.\[/g, '[');
  };
  let reachableSelf = 0;
  for (let p = 0; p < count; p++) reachableSelf += self(order[p]);
  let totalSelf = 0;
  const byType = new Map();
  for (let n = 0; n < N; n++) {
    totalSelf += self(n);
    const t = typeOf(n), row = byType.get(t) ?? { type: t, count: 0, self: 0 };
    row.count++; row.self += self(n); byType.set(t, row);
  }
  const classes = classNames.map((name, i) => ({ name, count: cCount[i], self: cSelf[i], retained: cRetained[i] }))
    .filter((_, i) => i !== classIdx[R]); // the synthetic root retains everything
  // Drill-down: the dominator tree pruned to owners of at least minMiB.
  const drill = [];
  const walk = (p, depth) => {
    const children = [];
    for (let k = kidStart[p]; k < kidStart[p + 1]; k++) if (retained[kids[k]] >= minMiB * MiB) children.push(kids[k]);
    children.sort((a, b) => retained[b] - retained[a]);
    for (const q of children.slice(0, 8)) {
      if (drill.length >= 160) return;
      const n = order[q], v = via(n);
      drill.push({ depth, name: classOf(n), type: typeOf(n), retained: retained[q], self: self(n),
        via: v ? `${v.from === ROOT ? '' : `${classOf(v.from)}.`}${edgeName(v.edge)}` : '', path: depth <= 2 ? pathOf(n) : undefined });
      if (depth < 9) walk(q, depth + 1);
    }
  };
  walk(R, 0);
  // ArrayBuffer backing stores by the property path holding them (BufferAttribute.array, Map values, ...).
  const buffers = new Map();
  let bufferBytes = 0, bufferCount = 0;
  for (let p = 0; p < count; p++) {
    const n = order[p];
    if (typeOf(n) !== 'native' || !/ArrayBufferData|BackingStore/.test(nameOf(n))) continue;
    bufferBytes += self(n); bufferCount++;
    const keys = [];
    let at = n;
    for (let h = 0; h < 12 && keys.length < 3; h++) {
      const v = via(at);
      if (!v) break;
      const t = edges[v.edge * EF + eType];
      if (t === PROPERTY || t === CONTEXT) { const k = edgeName(v.edge); if (!['buffer', 'backing_store'].includes(k)) keys.push(k); }
      else if (t !== INTERNAL && t !== HIDDEN && t !== ELEMENT && keys.length === 0) keys.push(edgeName(v.edge));
      if (v.from === ROOT) break;
      at = v.from;
    }
    const key = keys.reverse().join('.') || '(unnamed)';
    const row = buffers.get(key) ?? { path: key, count: 0, bytes: 0 };
    row.count++; row.bytes += self(n); buffers.set(key, row);
  }
  // Strings grouped by their first 48 characters (shader sources, JSON, repeated keys).
  const stringGroups = new Map();
  let stringBytes = 0;
  for (let p = 0; p < count; p++) {
    const n = order[p], t = typeOf(n);
    if (t !== 'string' && t !== 'concatenated string' && t !== 'sliced string') continue;
    stringBytes += self(n);
    const key = nameOf(n).slice(0, 48);
    const row = stringGroups.get(key) ?? { preview: key, count: 0, bytes: 0 };
    row.count++; row.bytes += self(n); stringGroups.set(key, row);
  }
  const sortBy = (rows, k) => [...rows].sort((a, b) => b[k] - a[k]);
  return {
    nodes: N, edges: E, reachable: count, totalSelf, reachableSelf,
    byType: sortBy(byType.values(), 'self'),
    classesByRetained: sortBy(classes, 'retained').slice(0, top),
    classesBySelf: sortBy(classes, 'self').slice(0, top),
    drill, buffers: { bytes: bufferBytes, count: bufferCount, paths: sortBy(buffers.values(), 'bytes').slice(0, top) },
    strings: { bytes: stringBytes, groups: sortBy(stringGroups.values(), 'bytes').slice(0, Math.min(top, 20)) },
    retainedOf: name => classes.find(c => c.name === name)?.retained ?? 0,
  };
}

const fmt = bytes => (bytes / MiB).toFixed(1).padStart(8);

export function report(a, title = 'heap snapshot') {
  const lines = [`${title}: ${a.nodes.toLocaleString('en')} nodes, ${a.edges.toLocaleString('en')} edges, `
    + `${(a.totalSelf / MiB).toFixed(1)} MiB total, ${(a.reachableSelf / MiB).toFixed(1)} MiB reachable (${a.reachable.toLocaleString('en')} nodes)`];
  lines.push('', 'by node type (self MiB, count):');
  for (const r of a.byType.slice(0, 12)) lines.push(`${fmt(r.self)}  ${String(r.count).padStart(9)}  ${r.type}`);
  lines.push('', 'classes by retained size, outermost instances (retained MiB, self MiB, count):');
  for (const r of a.classesByRetained) lines.push(`${fmt(r.retained)}  ${fmt(r.self)}  ${String(r.count).padStart(9)}  ${r.name}`);
  lines.push('', 'classes by self size (self MiB, count):');
  for (const r of a.classesBySelf) lines.push(`${fmt(r.self)}  ${String(r.count).padStart(9)}  ${r.name}`);
  lines.push('', 'largest owners, dominator tree (retained MiB, self MiB, class via retainer.edge; path from the root):');
  for (const d of a.drill) lines.push(`${fmt(d.retained)}  ${fmt(d.self)}  ${'  '.repeat(d.depth)}${d.name} [${d.type}]  via ${d.via}${d.path ? `  @ ${d.path}` : ''}`);
  lines.push('', `ArrayBuffer backing stores: ${(a.buffers.bytes / MiB).toFixed(1)} MiB in ${a.buffers.count.toLocaleString('en')}, by holding property path (MiB, count):`);
  for (const r of a.buffers.paths) lines.push(`${fmt(r.bytes)}  ${String(r.count).padStart(9)}  ${r.path}`);
  lines.push('', `strings: ${(a.strings.bytes / MiB).toFixed(1)} MiB, largest groups by first 48 characters (MiB, count):`);
  for (const r of a.strings.groups) lines.push(`${fmt(r.bytes)}  ${String(r.count).padStart(9)}  ${JSON.stringify(r.preview)}`);
  return `${lines.join('\n')}\n`;
}

async function* fileChunks(file) {
  const input = fs.createReadStream(file, { highWaterMark: 4 * MiB });
  const stream = file.endsWith('.gz') ? input.pipe(zlib.createGunzip({ chunkSize: 4 * MiB })) : input;
  stream.setEncoding('utf8');
  for await (const chunk of stream) yield chunk;
}

export async function summarizeFile(file, opts = {}) {
  const snapshot = await readSnapshot(fileChunks(file));
  const a = analyze(snapshot, { top: Number(opts.top ?? 30), minMiB: Number(opts['min-mib'] ?? 4) });
  const { retainedOf, ...json } = a;
  return { text: report(a, path.basename(file)), json, analysis: a };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const [command, file] = opts._;
  if (command === 'take') await take(opts);
  else if (command === 'summarize' && file) {
    const s = await summarizeFile(file, opts);
    process.stdout.write(s.text);
    if (opts.json) fs.writeFileSync(opts.json, JSON.stringify(s.json, null, 1));
  } else {
    console.error('usage: heap-snapshot.mjs take --cdp URL [--url PART | --open URL | --ws WS] [--wait done|now] [--timeout S] [--out DIR] [--workers] [--no-summary]\n'
      + '       heap-snapshot.mjs summarize FILE.heapsnapshot[.gz] [--top N] [--min-mib M] [--json OUT]');
    process.exitCode = 2;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main().catch(error => { console.error(error.message ?? error); process.exit(1); });
