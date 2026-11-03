#!/usr/bin/env node
// Run one bench lab scenario (game/src/qa/lab) in an existing Chrome over CDP and save its result JSON.
//
//   node tools/perf/run-bench.mjs 'bench=install&tiles=t0_0_0,t0_1_0' [--repeat N] [--out DIR]
//        [--cdp http://127.0.0.1:5223] [--base http://localhost:5220/] [--timeout 900] [--no-trace] [--keep-tab]
//
// Opens a new tab at <base>?<query>, waits for window.__bench.done, writes <out>/<stamp>-<label>.json, closes the tab.
// While the scenario measures, a Chrome trace (devtools.timeline, toplevel, user timing, v8.gc) runs: the page's
// bench:* marks delimit the measured windows and each tile, and the trace gives exact GC counts, GC time, heap freed
// and main-thread busy time per window (`result.trace`). In-page heap sizes are bucketed unless Chrome runs with
// --enable-precise-memory-info, so the trace is the GC source. Prints one line per run and a per-tile table.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TRACE_CATEGORIES = ['devtools.timeline', 'toplevel', 'blink.user_timing', 'disabled-by-default-v8.gc'];
/** Phases after which the page measures: tracing starts on the first of them. */
const MEASURING = new Set(['warm', 'baseline', 'measure', 'install', 'fly', 'save']);
const GC_PRIMARY = { MinorGC: 'minor', MajorGC: 'major' };
const GC_FALLBACK = { 'V8.GC_SCAVENGER': 'minor', 'V8.GC_MINOR_MARK_SWEEPER': 'minor', 'V8.GC_MARK_COMPACTOR': 'major' };

const r2 = v => Math.round(v * 100) / 100;

/** Compact the events a bench window needs: bench marks, GC events and main-thread tasks, keyed by pid:tid. */
export function collectTraceEvents(events, into = { marks: [], gc: [], tasks: [], threads: {} }) {
  for (const e of events) {
    const thread = `${e.pid}:${e.tid}`;
    if (e.ph === 'M' && e.name === 'thread_name') { into.threads[thread] = e.args?.name; continue; }
    if (typeof e.name !== 'string') continue;
    if (e.name.startsWith('bench:') && String(e.cat).includes('user_timing')) {
      if (e.ph === 'b' || e.ph === 'e') continue; // measure spans; marks are instants
      into.marks.push({ name: e.name.slice(6), ts: e.ts, thread });
    } else if (e.ph === 'X' && (e.name in GC_PRIMARY || e.name in GC_FALLBACK)) {
      into.gc.push({ name: e.name, ts: e.ts, dur: e.dur ?? 0, thread,
        before: e.args?.usedHeapSizeBefore ?? e.args?.data?.usedHeapSizeBefore ?? null,
        after: e.args?.usedHeapSizeAfter ?? e.args?.data?.usedHeapSizeAfter ?? null });
    } else if (e.ph === 'X' && e.name === 'RunTask' && String(e.cat).includes('toplevel')) {
      into.tasks.push({ ts: e.ts, dur: e.dur ?? 0, thread });
    }
  }
  return into;
}

/** GC counts and time, heap freed and main-thread busy time between two trace timestamps (µs) on `thread`. */
export function traceWindow(collected, thread, from, to) {
  const gcs = collected.gc.filter(g => g.thread === thread && g.ts >= from && g.ts <= to);
  const primary = gcs.some(g => g.name in GC_PRIMARY);
  const counted = gcs.filter(g => primary ? g.name in GC_PRIMARY : g.name in GC_FALLBACK);
  const kind = g => GC_PRIMARY[g.name] ?? GC_FALLBACK[g.name];
  const freed = counted.reduce((a, g) => a + (g.before != null && g.after != null ? Math.max(0, g.before - g.after) : 0), 0);
  let busy = 0, longest = 0;
  for (const t of collected.tasks) {
    if (t.thread !== thread) continue;
    const start = Math.max(from, t.ts), end = Math.min(to, t.ts + t.dur);
    if (end > start) { busy += end - start; longest = Math.max(longest, t.dur); }
  }
  return {
    ms: r2((to - from) / 1000), source: primary ? 'MinorGC/MajorGC' : counted.length ? 'V8.GC_*' : 'none',
    gc: { minor: counted.filter(g => kind(g) === 'minor').length, major: counted.filter(g => kind(g) === 'major').length,
      ms: r2(counted.reduce((a, g) => a + g.dur, 0) / 1000), freedMB: r2(freed / 1048576) },
    mainThread: { busyMs: r2(busy / 1000), longestTaskMs: r2(longest / 1000) },
  };
}

/** The trace's view of a lab result: the measured window, the install window and every tile. */
export function analyzeTrace(collected) {
  if (!collected.marks.length) return { error: 'no bench:* marks in the trace' };
  const counts = new Map();
  for (const m of collected.marks) counts.set(m.thread, (counts.get(m.thread) ?? 0) + 1);
  const thread = [...counts].sort((a, b) => b[1] - a[1])[0][0];
  const marks = new Map(collected.marks.filter(m => m.thread === thread).map(m => [m.name, m.ts]));
  const out = { thread, threadName: collected.threads[thread] ?? null, windows: {}, tiles: {} };
  for (const [name, ts] of marks) {
    const m = /^(.+):measure:start$/.exec(name);
    const end = m && marks.get(`${m[1]}:measure:end`);
    if (m && end != null) out.windows[m[1]] = traceWindow(collected, thread, ts, end);
    const t = /^tile:(.+):start$/.exec(name);
    const tileEnd = t && marks.get(`tile:${t[1]}:end`);
    if (t && tileEnd != null) out.tiles[t[1]] = traceWindow(collected, thread, ts, tileEnd);
  }
  const starts = [...marks].filter(([n]) => /^tile:.+:start$/.test(n)).map(([, ts]) => ts);
  const ends = [...marks].filter(([n]) => /^tile:.+:end$/.test(n)).map(([, ts]) => ts);
  if (starts.length && ends.length) out.windows.install = traceWindow(collected, thread, Math.min(...starts), Math.max(...ends));
  return out;
}

const fmt = d => d && d.p50 != null ? `${d.p50}/${d.p95}/${d.p99}` : '-';
/** One summary line of a lab result (and its trace window). */
export function summaryLine(result) {
  const s = result.summary ?? {}, trace = result.trace?.windows?.[result.mode] ?? result.trace?.windows?.install;
  const gc = trace ? `gc ${trace.gc.minor}+${trace.gc.major} (${trace.gc.ms} ms)` : 'gc -';
  return `${result.label}: ${s.fps ?? '-'} fps  cpu ${fmt(s.cpuMs)}  gpu ${fmt(s.gpuMs)}  interval ${fmt(s.intervalMs)}  `
    + `max ${s.intervalMs?.max ?? '-'} ms  draws ${s.draws?.p50 ?? '-'}  tris ${s.tris?.p50 ?? '-'}  ${gc}`
    + (trace ? `  main ${trace.mainThread.busyMs} ms` : '');
}

/** The per-tile table of an install result. */
export function tileTable(result) {
  if (!Array.isArray(result.tiles) || typeof result.tiles[0] !== 'object') return '';
  const head = ['tile', 'preview', 'full', 'frames', 'longest', 'stall', 'builds', 'gc', 'main', 'worst stage'];
  const rows = result.tiles.map(t => {
    const tr = result.trace?.tiles?.[t.id];
    const worst = Object.entries(t.stages ?? {}).filter(([k]) => k !== 'idle').sort((a, b) => b[1].stallMs - a[1].stallMs)[0];
    return [t.id, t.previewMs ?? '-', t.fullMs ?? '-', t.frames, t.longestMs ?? '-', t.stallMs, t.inFrameBuilds,
      tr ? `${tr.gc.minor}+${tr.gc.major}` : '-', tr ? tr.mainThread.busyMs : '-',
      worst ? `${worst[0]} ${worst[1].stallMs} ms` : '-'].map(String).concat(t.error ? [`ERROR ${t.error}`] : []);
  });
  const widths = head.map((h, i) => Math.max(h.length, ...rows.map(r => r[i].length)));
  return [head, ...rows].map(r => r.map((c, i) => i < head.length - 1 ? c.padEnd(widths[i]) : c).join('  ').trimEnd()).join('\n');
}

class Cdp {
  constructor(url) { this.url = url; this.seq = 0; this.pending = new Map(); this.listeners = new Set(); }
  async open() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP connection timed out')), 15000);
      this.socket.onopen = () => { clearTimeout(timer); resolve(); };
      this.socket.onerror = error => { clearTimeout(timer); reject(error); };
    });
    this.socket.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      if (msg.id && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id); this.pending.delete(msg.id); clearTimeout(p.timer);
        msg.error ? p.reject(new Error(`${p.method}: ${JSON.stringify(msg.error)}`)) : p.resolve(msg.result);
      } else if (msg.method) for (const fn of this.listeners) fn(msg);
    };
  }
  send(method, params = {}, sessionId) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 120000);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  close() { this.socket?.close(); }
}

async function runOnce(options, query, index) {
  let opts = options;
  const version = await fetch(`${opts.cdp}/json/version`, { signal: AbortSignal.timeout(10000) }).then(r => r.json());
  const cdp = new Cdp(version.webSocketDebuggerUrl);
  await cdp.open();
  const collected = { marks: [], gc: [], tasks: [], threads: {} };
  let tracing = false, traceDone = null, targetId = null;
  cdp.listeners.add(msg => {
    if (msg.method === 'Tracing.dataCollected') collectTraceEvents(msg.params.value, collected);
    if (msg.method === 'Tracing.tracingComplete') traceDone?.();
  });
  const url = `${opts.base}?${query.replace(/^\?/, '')}`;
  const started = new Date();
  try {
    ({ targetId } = await cdp.send('Target.createTarget', { url: 'about:blank', background: false }));
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const page = (method, params) => cdp.send(method, params, sessionId);
    await page('Runtime.enable');
    await page('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    await cdp.send('Target.activateTarget', { targetId }).catch(() => {});
    await page('Page.navigate', { url });
    const evaluate = async expression => (await page('Runtime.evaluate', { expression, returnByValue: true }))?.result?.value;
    const deadline = Date.now() + opts.timeout * 1000;
    let last = '';
    for (;;) {
      if (Date.now() > deadline) throw new Error(`timed out after ${opts.timeout} s (last: ${last})`);
      const status = await evaluate(`window.__bench ? JSON.stringify({ phase: window.__bench.phase, done: window.__bench.done,
        error: window.__bench.error ?? null, progress: window.__bench.progress ?? '' }) : JSON.stringify({ phase: 'boot', error: window.__qaError ?? null })`)
        .catch(() => null);
      const s = status ? JSON.parse(status) : { phase: 'navigating' };
      const line = `${s.phase}${s.progress ? ` ${s.progress}` : ''}`;
      if (line !== last) { console.log(`[run ${index + 1}] ${line}`); last = line; }
      if (s.error) throw new Error(s.error);
      if (!tracing && opts.trace && MEASURING.has(s.phase)) {
        try {
          await cdp.send('Tracing.start', { transferMode: 'ReportEvents',
            traceConfig: { includedCategories: TRACE_CATEGORIES, recordMode: 'recordContinuously' } });
          tracing = true;
        } catch (error) { console.warn(`[run ${index + 1}] no trace (GC counts unavailable): ${error.message}`); opts = { ...opts, trace: false }; }
      }
      if (s.done) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    const result = JSON.parse(await evaluate('JSON.stringify(window.__bench.result)'));
    if (tracing) {
      const complete = new Promise(resolve => { traceDone = resolve; });
      await cdp.send('Tracing.end'); await complete; tracing = false;
      result.trace = analyzeTrace(collected);
    }
    result.runner = { query, url, run: index + 1, startedAt: started.toISOString(), browser: version.Browser ?? null,
      traceCategories: opts.trace ? TRACE_CATEGORIES : null };
    return result;
  } finally {
    if (tracing) await cdp.send('Tracing.end').catch(() => {});
    if (targetId && !opts.keepTab) await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
    cdp.close();
  }
}

function parseArgs(argv) {
  const opts = { repeat: 1, out: path.join(os.homedir(), 'turku3d/captures/bench-lab'), cdp: 'http://127.0.0.1:5223',
    base: 'http://localhost:5220/', timeout: 900, trace: true, keepTab: false, query: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], next = () => argv[++i];
    if (a === '--repeat') opts.repeat = Number(next());
    else if (a === '--out') opts.out = next();
    else if (a === '--cdp') opts.cdp = next();
    else if (a === '--base') opts.base = next();
    else if (a === '--timeout') opts.timeout = Number(next());
    else if (a === '--no-trace') opts.trace = false;
    else if (a === '--keep-tab') opts.keepTab = true;
    else if (!opts.query && !a.startsWith('--')) opts.query = a;
    else throw new Error(`unknown argument ${a}`);
  }
  if (!opts.query || !/(^|&|\?)bench=(static|install|fly)(&|$)/.test(opts.query)) throw new Error("usage: run-bench.mjs 'bench=static|install|fly&...' [--repeat N] [--out DIR]");
  if (!(Number.isInteger(opts.repeat) && opts.repeat >= 1)) throw new Error('--repeat takes an integer >= 1');
  return opts;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  fs.mkdirSync(opts.out, { recursive: true });
  const results = [];
  for (let i = 0; i < opts.repeat; i++) {
    const result = await runOnce(opts, opts.query, i);
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '').replace('T', '-');
    const file = path.join(opts.out, `${stamp}-${String(result.label ?? result.mode).replace(/[^\w.=,-]+/g, '_')}-r${i + 1}.json`);
    fs.writeFileSync(file, JSON.stringify(result, null, 1));
    console.log(summaryLine(result));
    const table = tileTable(result);
    if (table) console.log(table);
    console.log(`saved ${file}${result.capture ? `  capture ${result.capture}` : ''}`);
    results.push(result);
  }
  if (results.length > 1) {
    const fps = results.map(r => r.summary?.fps).filter(Number.isFinite), p99 = results.map(r => r.summary?.intervalMs?.p99).filter(Number.isFinite);
    const spread = v => v.length ? `${r2(Math.min(...v))}..${r2(Math.max(...v))} (mean ${r2(v.reduce((a, b) => a + b, 0) / v.length)})` : '-';
    console.log(`repeat ${results.length}: fps ${spread(fps)}  interval p99 ${spread(p99)}`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(String(error?.stack ?? error)); process.exit(1); });
}
