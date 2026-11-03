import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analyzeTrace, collectTraceEvents, summaryLine, tileTable, traceWindow } from './run-bench.mjs';

const main = { pid: 7, tid: 1 }, other = { pid: 9, tid: 1 };
const mark = (name, ts, at = main) => ({ ...at, ph: 'R', cat: 'blink.user_timing', name: `bench:${name}`, ts });
const gc = (name, ts, dur, before, after, at = main) => ({ ...at, ph: 'X', cat: 'devtools.timeline,v8', name, ts, dur,
  args: { usedHeapSizeBefore: before, usedHeapSizeAfter: after } });
const task = (ts, dur, at = main) => ({ ...at, ph: 'X', cat: 'disabled-by-default-devtools.timeline,toplevel', name: 'RunTask', ts, dur });

test('trace windows count GCs, heap freed and main-thread busy time on the page thread only', () => {
  const collected = collectTraceEvents([
    { ...main, ph: 'M', name: 'thread_name', args: { name: 'CrRendererMain' } },
    mark('install:measure:start', 0), mark('install:measure:end', 500_000),
    mark('tile:t0_0_0:start', 1_000_000), mark('tile:t0_0_0:end', 3_000_000),
    mark('tile:t0_1_0:start', 3_000_000), mark('tile:t0_1_0:end', 4_000_000),
    gc('MinorGC', 1_100_000, 2000, 50 * 1048576, 40 * 1048576), gc('MinorGC', 1_200_000, 3000, 45 * 1048576, 41 * 1048576),
    gc('MajorGC', 3_500_000, 20000, 90 * 1048576, 60 * 1048576), gc('V8.GC_SCAVENGER', 1_100_000, 1500, null, null),
    gc('MinorGC', 1_150_000, 9000, 1, 0, other),
    task(900_000, 200_000), task(1_500_000, 2_000_000), task(3_600_000, 100_000, other),
    { ...main, ph: 'X', cat: 'toplevel', name: 'ThreadControllerImpl::RunTask', ts: 1_500_000, dur: 2_000_000 },
    mark('tile:t0_9_9:start', 5, other),
  ]);
  const a = analyzeTrace(collected);
  assert.equal(a.thread, '7:1');
  assert.equal(a.threadName, 'CrRendererMain');
  assert.deepEqual(a.tiles.t0_0_0.gc, { minor: 2, major: 0, ms: 5, freedMB: 14 });
  assert.deepEqual(a.tiles.t0_1_0.gc, { minor: 0, major: 1, ms: 20, freedMB: 30 });
  assert.equal(a.tiles.t0_0_0.mainThread.busyMs, 1600);
  assert.equal(a.tiles.t0_0_0.mainThread.longestTaskMs, 2000);
  assert.equal(a.tiles.t0_0_0.mainThread.source, 'RunTask');
  const scheduler = collectTraceEvents([mark('static:measure:start', 0), mark('static:measure:end', 1_000_000),
    { ...main, ph: 'X', cat: 'toplevel', name: 'ThreadControllerImpl::RunTask', ts: 100, dur: 3000 }]);
  assert.deepEqual(analyzeTrace(scheduler).windows.static.mainThread, { busyMs: 3, longestTaskMs: 3, source: 'ThreadControllerImpl::RunTask' });
  assert.equal(a.tiles.t0_0_0.source, 'MinorGC/MajorGC');
  assert.equal(a.windows.install.gc.minor, 2);
  assert.equal(a.windows.install.gc.major, 1);
  assert.equal(a.tiles.t0_9_9, undefined);
});

test('falls back to V8 GC phases when the timeline GC events are absent', () => {
  const collected = collectTraceEvents([mark('static:measure:start', 0), mark('static:measure:end', 10_000_000),
    gc('V8.GC_SCAVENGER', 100, 500, null, null), gc('V8.GC_MINOR_MARK_SWEEPER', 200, 500, null, null), gc('V8.GC_MARK_COMPACTOR', 300, 9000, null, null)]);
  const w = analyzeTrace(collected).windows.static;
  assert.equal(w.source, 'V8.GC_*');
  assert.deepEqual(w.gc, { minor: 2, major: 1, ms: 10, freedMB: 0 });
  assert.equal(traceWindow(collected, '7:1', 0, 50).gc.minor, 0);
  assert.equal(analyzeTrace(collectTraceEvents([])).error, 'no bench:* marks in the trace');
});

test('prints a summary line and the per-tile table', () => {
  const d = (p50, p95, p99, max) => ({ p50, p95, p99, max });
  const result = { label: 'install/medium off=traffic,walkers', mode: 'install',
    summary: { fps: 41.2, cpuMs: d(9, 30, 200, 900), gpuMs: d(8, 12, 15, 20), intervalMs: d(16.7, 40, 250, 2400), draws: d(900), tris: d(4e6) },
    trace: { windows: { install: { gc: { minor: 54, major: 1, ms: 120 }, mainThread: { busyMs: 9000 } } },
      tiles: { t0_0_0: { gc: { minor: 50, major: 1 }, mainThread: { busyMs: 3200 } } } },
    tiles: [{ id: 't0_0_0', previewMs: 800, fullMs: 4200, frames: 300, longestMs: 2400, stallMs: 3100, inFrameBuilds: 12,
      stages: { glb: { stallMs: 200 }, prepare: { stallMs: 2600 }, idle: { stallMs: 9000 } }, error: null },
    { id: 't0_1_0', previewMs: null, fullMs: null, frames: 10, longestMs: 20, stallMs: 0, inFrameBuilds: 0, stages: {}, error: 'boom' }] };
  const line = summaryLine(result);
  assert.match(line, /^install\/medium off=traffic,walkers: 41\.2 fps {2}cpu 9\/30\/200 {2}gpu 8\/12\/15 {2}interval 16\.7\/40\/250 {2}max 2400 ms/);
  assert.match(line, /gc 54\+1 \(120 ms\) {2}main 9000 ms$/);
  const table = tileTable(result).split('\n');
  assert.match(table[0], /^tile\s+preview\s+full\s+frames\s+longest\s+stall\s+builds\s+gc\s+main\s+worst stage$/);
  assert.match(table[1], /^t0_0_0\s+800\s+4200\s+300\s+2400\s+3100\s+12\s+50\+1\s+3200\s+prepare 2600 ms$/);
  assert.match(table[2], /ERROR boom$/);
  assert.equal(tileTable({ tiles: ['t0_0_0'] }), '');
});
