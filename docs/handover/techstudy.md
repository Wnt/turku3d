# techstudy handover: GTA V and Bodycam research for Turku3D (2026-11-03/04)

techstudy was a research loop. It wrote no game code. It studied how GTA V Enhanced and Bodycam (UE 5.5) keep big
scenes fast, and turned that into techniques and bench A/Bs for perf2/perf3, lodblend, stream2, npc, water and
floodlights2.

The only code in git from this loop is the GTA bridge `/quit` fix. It lives in the research folder, not in this repo.

## Where things are (local only, never in git)
- **Report:** `~/turku3d/loops/techstudy/REPORT.md`.
  - §2–4: GTA numbers next to ours, 16 ranked techniques, 5 briefs.
  - §7: live measurements.
  - §8: water and floodlit venues.
- **Working log:** `~/turku3d/loops/techstudy/STATUS.md`.
- **Static GTA numbers:** `~/turku3d/loops/techstudy/notes/gta-static-20261103.md`.
- **Probe:** a read-only CodeWalker .NET tool.
  - Output: `probe/*.jsonl` (statistics only).
  - Source: `probe/Tech.cs` plus the earlier gta-map probe.
  - PC copy: `C:\Research\techstudy\probe`.
- **Frames, clips, PIX summaries:** `~/turku3d/loops/techstudy/captures/`.
- **PIX captures:** `C:\Research\gta\captures\ts-*.wpix`, about 4.8 GB. They stay on the PC.
- **PIX timing tools:**
  - `~/turku3d/loops/techstudy/pixtool/ts_timing.cpp`, built on the PC as `C:\Research\techstudy\pixtool\ts_timing.exe`;
  - `ps/ts_seg.py` (per-pass segments);
  - `ps/events.py` (event-list draw counts).
- **Rule:** nothing from GTA, Bodycam or their captures goes into git. Screenshots stay local.

## Running GTA studies on the Windows host (`ssh jonni@192.168.1.250`)
1. **Take the shared lock.**
   - Check `C:\Research\gta\game.lock` first.
   - If another agent holds it (e.g. winperf measuring Chrome), wait. Don't run GTA, PIX replays or heavy probes then.
   - To take it, write `<you> <ISO time> <minutes>` and delete it when done.
2. **Launch with the bridge** (`~/turku3d/research/gta-bridge/gta.py`, token in `~/.config/turku3d/`):
   - `./gta.py launch --preset lowest [--capture pix]` reaches story mode in about 95 s, with the city empty.
   - `./gta.py normal` brings back traffic and peds.
   - The landing-page clicks sometimes stop on the GTA+ tab. Quit and launch again.
3. **Quit with `./gta.py quit --force`.**
   - Since 2026-11-03 it also ends the `PlayGTAV.exe` processes of the bridge's own launch.
   - Those used to keep Epic's tile on "Running", so the next launch failed with LS-0018 "already running".
   - If Epic still shows "Running" with no game: `taskkill /f /im PlayGTAV.exe`. Epic itself needs no restart.
   - If Epic is truly stuck, restart it in Jonni's session through the TurkuCore job (`src/core/runtask.sh`), never with
     a window over his desktop.
4. **Useful tools.**
   - Camera and focus:
     - `/camera`;
     - the native `SET_FOCUS_POS_AND_VEL` (0xBB7454BAFF08FE25). GTA's LOD follows this focus, so moving it with a
       fixed camera isolates LOD switches.
   - Measurement: `/frame_stats` (CPU/GPU), `/frame`, `/clip` (crf 0 for lossless), `/gpu_capture` (PIX).
   - Population: `/population?list=true` plus the natives `GET_CLOSEST_VEHICLE` / `GET_VEHICLE_IS_DUMMY` for car tiers.
   - Helpers: `~/turku3d/loops/techstudy/live.py`, `watersess.py`, `speedrun.py`.
5. **PIX on the PC, with the game closed.**
   - Event lists: `pixtool open-capture X.wpix save-event-list X.csv`.
   - Per-event GPU timing:
     - upgrade first with `pixtool upgrade-gpu-capture X.wpix --dest=Y.wpix` (2610.28 tools);
     - then run `ts_timing.exe Y.wpix <prefix>` and `ts_seg.py`.
   - Replay times run about 3x live; compare them relatively.
6. **The PC is GPU-bound at 2880x1800 on the Radeon 560X** (about 32 ms busy at every pose). Frame timing doesn't
   separate passes; use PIX.

## Findings applied (merged into integration by their owners)
| Finding (REPORT) | Change | Owner, integration SHA |
| --- | --- | --- |
| Most GTA cars are dummies; real physics for about 5 cars within 35 m (§7 L4) | car tiers: real / dummy lane heights / far 5 Hz; real <=5 moving cars <35 m | npc 4c70ad3b, 2a28f7ce |
| HD band is about 250 m in 3D; fliers see LOD below (§7 L1, L5) | high cameras install full tiles only within 250 m 3D | stream2 507390bb |
| LOD on 3D distance (§7 L1) | 3D quadtree refinement above 250 m; fade length tunable (`lodfade=`, still 0.6 s) | lodblend dd6f8f21 |
| No small shadow casters at altitude (§7 L3) | above about 100 m, small casters leave the cascades (`?altitudeShadows=0`) | perf3 b3880c13 |
| Static render bundles (T3, brief 3) | per-cell BundleGroups, **off by default** (`?bundles=1`): per tile it was a net loss | perf3 3ea7673e, dce9a1e0 |
| Water W1: no planar pass on weak GPUs | reduced tier reflects through a screen-space march (`?water=planar\|ssr\|sky`) | water 163ff960, 1e5cc203 |
| Water W5: tight GTA sun glint | tight lobe, hidden behind bank buildings | water 2a3dcc7d |
| Water W3/W6/W8/W4 and W7 motion | bank fade, night sprite mirror, rain, flow on sloped reaches, river motion vectors | water (see `docs/handover/water.md`) |
| Venue lighting: pools and coronas (§8.4) | GTA reference used in the venue light model | floodlights2 (see `docs/handover/floodlights2.md`) |

## Still open, ranked
1. **Cooked tiles (T2, brief 2).** Move styling, static-batch merges and facade packing into a tilepatch migration with
   generator parity. A full-tile install is still 6–9 s of main-thread work.
2. **Pipeline warm-up list (T4, brief 4).** Record each session's program keys, prewarm them at the menu, and keep
   WGSL byte-stable. On Metal, Dawn caches only Tint->MSL, so the compile repeats every session.
3. **Short swaps (§7 L2).** GTA swaps HD/LOD within 2 frames and streams textures afterwards. Run the A/B
   `bench=fly&lodfade=0.1` against the default 0.6.
4. **Water W2 with block shells, flow on flat reaches, and generator foam (W4/W9).** See the water handover, open
   items 1–2.
5. **Allocation-free frames (T6):** about 680 MB/s main-thread allocation.
6. **Optional-priority detail (T8):** 31.7 % of GTA detail entities are optional.
7. **HD texture tiers by distance (T9):** GTA's HD texture distance p50 200 m, 10 m for small props.
8. **Wider occlusion (T11), night light sprites in the far city (T13), streaming relevance bounds (T15).**
9. **Not measured:**
   - GTA population density against player speed;
   - a street-level floodlit sports field;
   - GTA at higher presets (the planar water pass may return there).
