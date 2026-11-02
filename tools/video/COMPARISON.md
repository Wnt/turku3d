# Production / Gen 2 comparison

Create the 60-second, 1920×1080, 60 fps film:

```sh
node tools/video/make-comparison.mjs --out ~/turku3d/video/2026-11-02
```

Options: `--left URL`, `--right URL`, `--shots FILE`, `--out DIR`, `--preview`
(previews only), `--skip-preview` (reuse a reviewed preview). Defaults use `main`
in an isolated local Vite snapshot on 5960 against the published-tiles proxy 5195,
and the integration server on 5220. Passing the published Pages URL selects the
same local production fallback. Nothing writes to production, main, or raw data.
Local servers started by the command are stopped when it finishes.

Prerequisites: the repository's Node dependencies, ffmpeg/ffprobe, the existing
pipeline Python environment (Pillow and NumPy), the shared tile farm, 5195, 5220,
and the Chrome guard. No paid tools or new keys. Browser profile and logs live in
`~/turku3d/loops/video/`; only one guarded automation browser is used at a time.
Close any manually held video browser before running the complete command.

`comparison-shots.json` contains the 15 camera paths, hours, weather presets and
wind values. Paths use EPSG:3877 E/N, absolute N2000 eye height, compass yaw,
positive-up pitch, and vertical FOV. A Hermite ease gives smooth camera velocity.
Walking and driving use deterministic camera/actor rigs on surveyed street
alignments, with the game's own character and sedan assets. They do not measure
live input latency or vehicle physics. The character animates at fixed steps;
vehicle wheels turn with the scripted route distance.

The command registers the integration commit as `video-<sha>`, requests every
path tile plus a one-tile ring, and adds any extra detailed resident tiles observed
in preview. It waits for full, fresh products, then changes only `version` in
`loops/integrator/tile-service.json` (retaining `service` and `scenery`). It runs
the coordinator's 5220 verification with our guarded profile, then captures.

Capture reuses two paused pages in one browser, rendering the two sides of each
segment back to back. `beforeFrame`/`afterFrame`, the simulation clock, rain phase,
and `performance.now` advance one frame at a time. CDP screenshots pipe directly
to H.264 encoders; real-time FPS does not change the output timing. Camera poses
and resident visual hashes are stored with each clip. New-side stale manifests,
incomplete tiles and mismatched resident hashes are rejected. The frozen
production host itself marks some harbour tiles stale; those are retained as
published, with the flags recorded, rather than rebuilding production.

Both sides use the same astronomical calendar instant and camera. Most shots use
8 September; the final shot uses a bright 21 June night so production stays legible. Production
has no weather or wind selector and uses its existing clear sky/static water.
The new side uses each named preset's existing wind value. Neither build exposes
a foliage-season selector; existing vegetation is preserved. UI and cursors are hidden;
labels, divider, title/end cards and credits are added only during editing.
The Föri segment shows the crossing area; the captured view has no distinct ferry
model. Landmark geometry is whatever each pinned game build actually renders.

The film uses 15 × 4-second segments, 2.8/3.6-second title/end cards, and sixteen
0.4-second crossfades (60 seconds total). Composition adds restrained grading,
varied transitions and an original local synthesized ambient score. No external
recordings, photographs or samples are used. H.264 capture intermediates use CRF 12; the edit uses CRF 14.

Outputs: `turku3d-before-after.mp4`, `contact-sheet.jpg`, `review.json`, build and
tile receipts, `preview/`, `clips/`, and `edit/`. Inspect the contact sheet before
sharing. `comparison-review.py` checks dimensions, frame count, duration and
black intervals. Resume validates the shot, capture code, resolution, frame rate and pinned source revision before reusing
a clip. Composition is rebuilt from the validated clips. Use a new output directory
to preserve an earlier comparison.

Focused checks:

```sh
node --test tools/video/comparison.test.mjs
node --check tools/video/comparison-capture.mjs
```

The older explainer/showcase pipeline and its `shots.json` are independent; see
[README.md](README.md).
