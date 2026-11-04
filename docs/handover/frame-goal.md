# Frame pacing release goal (2026-11-04)

Jonni: "Set a goal that would be acceptable for a commercial game release. I think we cannot have any slow frames."
Every perf agent measures against this page. Owner of the definition: the `framepace` agent; change it only with
Jonni's OK.

## Targets

Measured on the standard drive replay `20261103-144904-0rb85a` segment 1 (about 96 s), play phase only, from the
frame intervals in `frames.jsonl` (`~/turku3d/loops/framepace/frames.py` prints every column below).

| Tier (reference machine) | Mean FPS | p99 frame | Frames > 33.4 ms | Frames > 50 ms | Frames > 100 ms |
|---|---|---|---|---|---|
| medium (M1 Max) | ≥ 50 | ≤ 1.5 × median and ≤ 33.4 ms | ≤ 0.2 % (about 10 per drive) | **0** | **0** |
| reduced (Radeon 560X, M1 Air) | ≥ 40 | ≤ 1.5 × median and ≤ 40 ms | ≤ 1 % | **0** | **0** |

Cause rules, which hold on every tier during play (after the menu or the bench settle):

- No in-frame shader or pipeline build (`gfx.build` events in the capture, `js:shader-build` hitches): everything a
  frame can draw is prepared before it is drawn.
- No main-thread task over 8 ms from streaming, installs, collision, traffic graph or population work: such work is
  budgeted per frame, moved to a worker, or prepared ahead.
- No GC pause over 4 ms (major or incremental-finalize marks); minor scavenges stay under 1 ms each. Steady play
  allocates under 50 KB per frame.
- Physics-worker stalls never show on screen: render extrapolation hides gaps up to 150 ms, and worker work is
  budgeted so that a gap never exceeds that.

Live play (not the replay) has the same targets after the first 10 s past the menu (the first load). A spawn,
teleport or respawn may hitch once, behind a fade.

## Why these numbers

- 50 ms is three refresh intervals at 60 Hz: any player sees it as a stutter, and commercial frame-time QA treats a
  spike above twice the frame budget as a defect. The budget is 16.7–20 ms on medium and 25 ms on reduced, so 50 ms
  is the hard line on both and must never happen.
- p99 ≤ 1.5 × median is the PC-benchmark "1 % low ≥ two thirds of the average" rule: the frame-time line stays
  flat enough that vsync and VRR pacing look smooth, not just fast on average.
- The 33.4 ms allowance (two vsyncs missed) is small but not zero, because a browser cannot pin threads or control
  the OS scheduler. It is tighter on medium, which has the headroom.
- The cause rules turn the frame numbers into engineering rules, so a new feature can be checked without a
  replay: shader compilation, streaming, collision builds and GC are the four sources of every hitch measured so
  far (perf3, physstall, npc, stream2).

## How to measure

The benchlab method (`benchlab.md`): a frozen build on its own port, a fresh tab per run, one discarded warm-up,
interleaved variants, three runs per variant. Report the median of the three and the range. `tile_timeline.py`
attributes each hitch to its cause.
