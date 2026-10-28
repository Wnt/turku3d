## Result: DONE

Physics now advances independently of main-thread message delivery. On Jonni's
M1 MacBook Air, held-key publication age p50/p95/max fell from
112.17/324.57/702.93 ms to 11.15/21.79/42.19 ms. The worker publication clock
increased from 46.129 Hz to 60.002 Hz. Rendering still has separate stalls.

## What changed

- Gameplay uses a self-clocked physics worker with five-substep catch-up bounds,
  pause handling and a 500 ms input lease. Ordered explicit steps remain available
  for saves, loading, queries and deterministic tools. Timer work joins the worker's
  existing command chain, preserving tile-load and command ordering.
- Isolated pages exchange input/state through shared mailboxes read by the render
  loop. A bounded, nonblocking copy lock prevents data races; neither side waits or
  spins. Jump counters and cumulative acknowledged actor deltas survive skipped
  snapshots. Standing poles are omitted; sleeping actor poses are cached. Final
  poses/removals persist until consumed. Local pole-break notifications remain
  latched by ID for multiplayer/fines.
- Pages gains a scoped, non-caching COOP/COEP service worker and guarded bootstrap.
  Unsupported isolation falls back to self-clocked message transport with bounded
  outstanding replies. The physics README documents the cross-origin resource
  audit, scoped paths and transport contracts. Keyboard autorepeat returns early.
- Pure tests cover mailbox layout/contention, clock bounds, actor acknowledgements,
  client transports, bootstrap and keyboard repeat. The browser-budget parser
  recognizes inline brief assignments. Repeatable Air scenarios are in
  `tools/perf/air-scenarios/`; capture code is outside production.

Fresh matched Air captures used native 1600x913 CSS pixels at DPR 2, headed Chrome
156, default quality, the same published real tiles and Vite serving on both sides.
The baseline temporarily restored current main's game files and added only a worker
publication timestamp. All source was restored afterward. The phases were idle,
20 s W, reset/settle, 20 s W+A from standstill, 20 s W+D, and release. Autorepeat was
30 Hz. The recorder ran in light mode before worker construction.

| Held phase | Before age p50/p95/max (ms) | After age p50/p95/max (ms) | Physics ticks before → after |
| --- | --- | --- | --- |
| W | 71.15 / 151.78 / 409.18 | 11.09 / 21.29 / 42.19 | 995 → 1208 |
| W+A from rest | 102.11 / 214.88 / 296.71 | 11.33 / 22.04 / 27.01 | 858 → 1207 |
| W+D | 195.10 / 441.41 / 702.93 | 11.12 / 21.77 / 25.14 | 612 → 1225 |

These are timed inputs, not a path-following route. The car can hit a bench or wall,
as Jonni observed. A stationary car alone is not evidence of a freeze. State age
and worker ticks establish the delivery/clocking result, but route distance, handling
and rendering are not controlled comparisons.

Held-phase worker step p50/p95/max fell from 2.90/8.22/10.945 ms to
1.815/3.61/5.245 ms. The fixed steered-start maximum was 4.565 ms: no steering-specific
wheel-cast/curb spike warranted an algorithm change. A separate local Node diagnostic
with eight real office collision tiles, 720 steps and 2880 wheel casts found warm
steered-start max 2.142 ms and p99 0.975 ms; its first cold step was 39.786 ms.
Shared packets were about 3.87 KB idle and at most 19.03 KB during steering.

Render frame p50/p95/max was 27.795/32.67/325.495 ms before and
32.445/38.5/503.57 ms after. Whole-capture retained heap growth was +92.89 versus
+95.28 MiB; these GC-sensitive endpoints do not establish allocation rate.
Repeat-listener p95 was 0.01 ms on both sides. No rendering improvement is claimed;
physics travels farther under the same timed inputs after the fix.

Additional Air checks completed without page errors:

- Virtual standard controller: actual gamepad input, shared transport, maximum held
  state age 43.44 ms; publication clock 60.004 Hz.
- Final production bundle under `/turku-game/game/` on plain HTTP: scoped
  `/turku-game/game/isolation.js` established isolation/shared transport, maximum
  held age 47.495 ms; publication clock 59.997 Hz.
- Explicit service-worker bypass: nonisolated message fallback, publication clock
  59.999 Hz. Display age still reached 9066.1 ms under input starvation, but the
  worker advanced 4957 ticks over 82.65 s and caught up after release. This fallback
  removes acknowledgement-driven simulation stalls; it cannot guarantee fresh
  rendering when the browser withholds main-thread message tasks.

Raw evidence stays outside Git at
`$TURKU3D_WORK/reports/latency/air/{before,after,controller,pages,fallback}/`:
`state-age.json`, `state-seconds.json`, `summary.json`, `frames.json`, recorder log
and screenshot. The supplied recorder's nested-template escaping failed compile
validation; the scenario used a corrected `String.raw` copy in its report directory,
without editing the external recorder. Legacy one-reply-per-step `stuckMs` is invalid
for self-clocked transport; direct publication age/ticks are authoritative. Earlier
recordings can retain that legacy artifact. The first baseline with the recorder
error is retained separately and is not the matched comparison above.

## How to verify

```sh
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
npm --prefix game run build
# With exclusive Air availability and the sandbox Vite server running:
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs before
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs after
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs controller
# With the final bundle served on plain HTTP port base +4 as described in its README:
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs pages
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs fallback
```

The real tile release was `38e57affa9496d2bd51ab8472e80e662773828bb`, referenced
read-only from this sandbox's `dist/tiles`. Before rerunning baseline, record main's
revision because the scenario deliberately tests the current main. Exclusive Air
sessions were stopped after each capture and handed back to perf-air-render.

## Gate output

Pre-rebase fast gate passed (9.009 s full relevant run, latest cached run 0.912 s).
Policy, materials, lint, types and fast units passed; five budget tests passed
explicitly. Final production bundle compiled successfully in 1.25 s after all
transport/actor/locking changes. Final combined-main verification is recorded below.
No gate was weakened and no slow/browser test was added to the gate.

## Follow-ups

- Physical iPhone home-screen startup remains untested; manifest/bootstrap scope was
  inspected and nested static-host isolation was verified on Chrome.
- Nonisolated fallback can still display stale state under main-thread scheduling
  starvation; supported isolated Chrome uses the verified shared transport.
- Rendering frame stalls are tracked separately by perf-air-render.
