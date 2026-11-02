# Production / Gen 2 comparison

The November 2 urgent delivery is the 60-second, 1920×1080, 60 fps file
`~/turku3d/video/2026-11-02/turku3d-before-after.mp4`. Its fifteen audited production
clips were reused unchanged from `viewdist-final/clips/`. All fifteen new-side clips
were recaptured with explicit `traffic=1` and `walkers=1`; these are population caps,
so this cut permits one moving traffic vehicle and one walker, in addition to parked
vehicles and the scripted walk/drive actors. The normal capture defaults remain 40/100.

The new client came from the viewdist working tree at `bcf3a650` plus the game-owned
NPC replay, ground-contact and walker-continuity changes committed after delivery.
Near/far data stayed pinned to `101925fa` / `viewdist-101925fa`; the client revision is
not the tile revision. `viewdist-final/urgent-source.json` and the saved working-tree
patch record this distinction. The last six clips used three disjoint shot files and
separate guard owners/profiles, sharing the same Vite/proxy servers. No post-capture
review, contact sheet or NPC judge was run, by Jonni's urgent 21:15 instruction.
The earlier contact sheet does not describe this delivery.

Recompose this delivered cut from its preserved clips, without browsers or recapture:

```sh
cd ~/turku3d/wt/loops/viewdist/repo
bash pipeline/env/run.sh python tools/video/comparison-compose.py \
  --out ~/turku3d/video/2026-11-02/viewdist-final \
  --shots tools/video/comparison-shots.json
cp ~/turku3d/video/2026-11-02/viewdist-final/turku3d-before-after.mp4 \
  ~/turku3d/video/2026-11-02/turku3d-before-after.mp4
```

For right-only recapture, start the pinned tile proxy on5966 and this client on5962,
then run (existing production clips remain untouched):

```sh
TURKU3D_CAPTURE_OWNER=viewdist node tools/video/comparison-capture.mjs \
  --side right --left http://127.0.0.1:5960/ --right http://127.0.0.1:5962/ \
  --shots tools/video/comparison-shots.json \
  --out ~/turku3d/video/2026-11-02/viewdist-final \
  --revision 101925fab6ccd6054248d4cc8b17c55bb1cec65d \
  --traffic 1 --walkers 1 --skip-preview
```

`TURKU3D_CAPTURE_PROFILE` and `TURKU3D_CAPTURE_CDP_PORT` select a separate browser
profile and port when concurrent workers have been authorized. Each worker must
use a disjoint shot file, unique guard owner and profile. The urgent run used
`viewdist`, `viewdist-2`, `viewdist-3`, ports5963/5967/5968, with all profiles under
`~/turku3d/loops/viewdist/`.

For the ordinary full preparation, capture and review workflow, use:

```sh
cd ~/turku3d/wt/loops/viewdist/repo
TURKU3D_CAPTURE_OWNER=viewdist node tools/video/make-comparison.mjs \
  --left https://wnt.github.io/turku-game/ \
  --right http://localhost:5220/ \
  --shots tools/video/comparison-shots.json \
  --out ~/turku3d/video/2026-11-02
```

The delivered cut is `~/turku3d/video/2026-11-02/turku3d-before-after.mp4`.
The previous 16:24 cut is preserved beside it as
`turku3d-before-after-1624.mp4`; the earlier14:39 cut is also retained.
`builds.json` records the prepared production/integration commits and tile alias;
`urgent-source.json` takes precedence for the delivered new client.
Re-running selects the integration commit current at that time. Choose a new
`--out` directory to preserve the delivered cut and its capture receipts.

Options: `--left URL`, `--right URL`, `--shots FILE`, `--out DIR`, `--preview`
(previews only), `--skip-preview` (reuse a reviewed preview). Defaults use `main`
in an isolated local Vite snapshot on 5960 against the published-tiles proxy 5195,
and the integration server on 5220. Passing the published Pages URL selects the
same local production fallback. Nothing writes to production, main, or raw data.
Local servers started by the command are stopped when it finishes.
The read-only production relay retries transient upstream failures up to three
times. Successful content-addressed assets are cached in
`~/turku3d/loops/<owner>/published-cache/` with byte-integrity checks; manifests
remain live. Delete that directory to clear only this pipeline's local cache.

Prerequisites: the repository's Node dependencies, ffmpeg/ffprobe, the existing
pipeline Python environment (Pillow and NumPy), the shared tile farm, 5195, 5220,
and the Chrome guard. No paid tools or new keys. Browser profile and logs live in
`~/turku3d/loops/<owner>/`; only one guarded automation browser is used at a time.
Close any manually held video browser before running the complete command.

`comparison-shots.json` contains the 15 camera paths, hours, weather presets and
wind values. Paths use EPSG:3877 E/N, absolute N2000 eye height, compass yaw,
positive-up pitch, and vertical FOV. A Hermite ease gives smooth camera velocity.
Walking and driving use deterministic camera/actor rigs on surveyed street
alignments, with the game's own character and sedan assets. They do not measure
live input latency or vehicle physics. The character animates at fixed steps;
vehicle wheels turn with the scripted route distance.
QA mode omits parked vehicles, so capture restores each build's native parked-car
renderer using that side's streamed vehicle records. Gen 2 retains its occupancy
rules and resting-slot transforms; production retains its nearby-body range.
No parking positions are invented. Trucks are omitted. By default the new side enables the high-tier
NPC caps (`traffic=40`, `walkers=100`; these switches are counts, not booleans).
Camera mode runs the game's TrafficFlow solver, articulated fleet wheels and dusk/night
lamps, plus its ordinary pedestrian population and materials (including the removed glow).
The game-owned replay clock advances all NPC motion at exact frame times with seed20261102;
loading and screenshot latency do not advance it. Each new-side receipt includes actor poses.
The fifteen audited production clips are reused unchanged; pass `--reuse-production`
when re-running into their output directory. Their camera paths must match the new cut.

The command registers the integration commit as `<owner>-<sha>`, requests every
path tile plus a one-tile ring, and adds any extra detailed resident tiles observed
in preview. It waits for full, fresh products, then changes only `version` in
`loops/integrator/tile-service.json` (retaining `service` and `scenery`). It runs
the coordinator's 5220 verification with our guarded profile, then captures.

Capture reuses two paused pages in one browser, rendering the two sides of each
segment back to back. `beforeFrame`/`afterFrame`, the simulation clock, rain phase,
advance one frame at a time. Only the legacy production page substitutes
`performance.now`; the new game streams against its ordinary monotonic clock. CDP screenshots pipe directly
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
The labels state each side's actual weather and local hour. A graduated footer
keeps foreground road detail visible beneath the segment title.
The Föri segment shows the crossing area; the captured view has no distinct ferry
model. Landmark geometry is whatever each pinned game build actually renders.

The film uses 15 × 4-second segments, 2.8/3.6-second title/end cards, and sixteen
0.4-second crossfades (60 seconds total). Composition adds restrained grading,
varied transitions and an original local synthesized ambient score. No external
recordings, photographs or samples are used. H.264 capture intermediates use CRF 12; the edit uses CRF 14.
Scene transitions apply the same effect independently inside each comparison half.
The divider and labels are added afterward; captions switch once at the midpoint
instead of dissolving into doubled text. Clean scene intermediates use CRF 12.

Outputs: `turku3d-before-after.mp4`, `contact-sheet.jpg`, `review.json`, build and
tile receipts, `preview/`, `clips/`, and `edit/`. Inspect the contact sheet before
sharing. `comparison-review.py` checks dimensions, frame count, duration and
black intervals. Resume validates the shot, capture code, resolution, frame rate and pinned source revision before reusing
a clip. Composition is rebuilt from the validated clips. Use a new output directory
to preserve an earlier comparison.

For an editorial review at one unchanged camera pose, compose the saved full-size
midpoint frames without opening a browser or encoding the film:

```sh
bash pipeline/env/run.sh python tools/video/comparison-compose.py \
  --out ~/turku3d/video/2026-11-02 --still market-hall
```

This writes `edit/market-hall-still.png`. Compare two versions at the same shot;
retain its capture receipts as evidence that the scene and camera are unchanged.
Use `--still title` or `--still end` to review a card. The opening uses both matched
dawn frames with equal background shading and introduces the comparison explicitly.
The closing card shares that framing and treatment, with concise copy, the weather
caveat, date, and source attribution retained.

Render one transition from existing captures without rebuilding the whole film:

```sh
bash pipeline/env/run.sh python tools/video/comparison-compose.py \
  --out ~/turku3d/video/2026-11-02 --transition street-walk
```

This writes the walk-to-drive `edit/transition-09.mp4` (24 frames). Compare the same
frame index on both candidates; frame 12 is the midpoint. The command also writes
the two corresponding labelled and clean scene clips for the transition inputs.

Focused checks:

```sh
node --test tools/video/comparison.test.mjs
node --test tools/video/published-proxy.test.mjs
node --check tools/video/comparison-capture.mjs
```

Capture one full-resolution midpoint for a same-pose review (servers must already
be running; `--revision` is the pinned tile/source revision):

```sh
node tools/video/comparison-capture.mjs --still --only street-drive --side right \
  --revision <pinned-sha> --out ~/turku3d/loops/video/review
```

Use `--side left` or `--side both` as needed. The still uses the full clip's exact
midpoint pose and animation frame, warms temporal history there, and writes
`stills/<shot>-<side>-still.jpg` plus a receipt containing native parked IDs.

The older explainer/showcase pipeline and its `shots.json` are independent; see
[README.md](README.md).

View-distance revision: the shot list replaces riverboats, academy, library and Föri
with 95–135 m city-centre, cathedral, Kauppatori and bridge panoramas. Castle and
waterfront remain in slots 13/14. The new build uses ordinary game streaming; capture
does not replace its update function, preload a special route, or delete tile entries.
`TURKU3D_CAPTURE_OWNER` selects the guard owner, isolated profile and working directories.

The new-side capture runs a frozen local snapshot of the selected integration commit
on5961 against its pinned tile alias. This prevents other loops' subsequent HMR edits
from changing a shot.5220 still receives and verifies the actual game fix. Every new-side
frame must pass the game's `viewStatus()` readiness signal; each receipt stores far
source hashes and coverage. Captures can wait for streaming but cannot alter it.

Prepare the shot footprints without occupying a browser slot:

```sh
node tools/video/prepare-scenery.mjs --service http://127.0.0.1:5226 \
  --shots tools/video/comparison-shots.json --out /owned/scenery-ready.json
```

Preparation uses the game's actual tile-selection functions. The capture audit
checks all240 new-side frame receipts per shot and final manifest hashes before
composition, then verifies the encoded film and creates its contact sheet.

When another agent is waiting, capture releases its browser at a clip boundary
after ten minutes. Re-run the same command with `--resume`: it keeps the original
integration and production revisions and reuses matching completed clips and
previews. An interrupted clip is recaptured before the film can pass its audit.
