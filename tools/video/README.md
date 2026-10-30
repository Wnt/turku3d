# Turku3D pipeline video

The second cut is a 1920×1080, 30 fps explainer with moving game footage throughout,
29 distinct shots, physics-driven car shots, compact source overlays, and local
Kokoro `bf_emma` narration. The first cut remains available in Git history at `bff2d9e3`.
Final files are outside the repository in `~/turku3d/media/pipeline-video/`.

No street-level photograph is opened or included. `facade.py` reads a previously
generated parameter description and renders its procedural output. City source
images and all game frames carry City of Turku / CC BY 4.0 and OpenStreetMap / ODbL
attributions. The thumbnail comes from a captured game frame. There are no uploads,
external multiplayer connections, host gamepads, or paid services.

## Showcase v3 (30 October 2026)

The 118.8-second showcase adds real courier interactions, a paid delivery, driving,
walking, a three-client physics-driven convoy, local round UI, the July 2027 sun,
and an actual first/full tile replacement. Media stays outside Git and is not uploaded.
The browser adapter admits this sandbox for at most 60 browser-minutes; the complete
production task also has a separate one-hour wall limit. All gamepad access is disabled.

The generation demonstration runs the active OCI generator in a unique output
folder with shared input/stage caches. It does **not** measure a cold uncached public
request or alter public output. The edit trims the waiting period and is explicitly
labelled a time-lapse; the inset clock remains actual elapsed time. Local round time
is advanced for the results shot. The convoy uses three independent real clients
on the owned server, with live network replication and Rapier vehicle movement.
Fixed-step capture is not an FPS benchmark. Performance figures come from the cited
flight-loop report, including its worst frame. Coverage and failures are documented
in the delivered review log rather than described as complete.

Start from the assigned `showcase-video-v3` sandbox. Initialise only owned directories:

```sh
source ../sandbox.env
mkdir -p "$TURKU3D_WORK/video/"{shots,audio,edit,review,evidence}
npm ci --no-audit --no-fund
npm ci --prefix qa/render --no-audit --no-fund
npm ci --prefix game --no-audit --no-fund
npm ci --prefix server --no-audit --no-fund
python3 scripts/dev/host_slots.py build -- node tools/video/build.mjs
```

Keep these three servers in separate terminals, each with the sandbox environment:

```sh
node tools/video/showcase-relay.mjs
node tools/video/showcase-multiplayer.mjs
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --port "$((TURKU3D_PORT_BASE+1))"
```

The relay uses owned port +5, multiplayer +4, and dist +1. The relay reads deployed
OCI assets and maps the client's `/coarse/status` request to `/coarse/health`.
Capture warm imagery and gameplay sequentially:

```sh
python3 tools/video/browser.py node tools/video/showcase-capture.mjs music river_open
python3 tools/video/browser.py node tools/video/showcase-capture.mjs capture
python3 tools/video/browser.py node tools/video/showcase-play.mjs capture
python3 tools/video/browser.py node tools/video/showcase-play.mjs delivery
python3 tools/video/browser.py node tools/video/showcase-play.mjs rounds
python3 tools/video/browser.py node tools/video/showcase-convoy.mjs
```

For the isolated two-pass capture, the existing `oci-drafts` SSH configuration and
deployed Python environment are required. Reserve a free owned loopback port on OCI.
This task uses 5196 there and its own +7 tunnel locally. Each helper invocation gets
a fresh output directory; never clear public tile outputs to force a video event.

```sh
ssh -T -L "$((TURKU3D_PORT_BASE+7)):127.0.0.1:5196" oci-drafts \
  '~/turku3d-drafts/run.sh python -u -' < tools/video/showcase-oci.py
# In a separate sourced terminal:
python3 tools/video/browser.py node tools/video/showcase-generation.mjs
curl -fsS "http://127.0.0.1:$((TURKU3D_PORT_BASE+7))/capture/status" > "$TURKU3D_WORK/video/evidence/isolated-oci-cityhall.json"
curl -fsS "http://127.0.0.1:$((TURKU3D_PORT_BASE+7))/capture/stop"
```

The helper is deliberately scoped to tile `t0_1_0`, and stops its worker after both
passes. A repeat needs a fresh helper invocation. The production capture's first
pass took 36.535s and full pass another 61.521s; these are one sample, not p50 values.
Stop the helper only after both results are saved. The evidence JSON records its
unique remote output path and process, permitting precise cleanup without touching
another task. The public health snapshot must be refreshed for a new dated cut.

```sh
HF_HOME="$HOME/turku3d/tools/voice/hf" HF_HUB_OFFLINE=1 \
  python3 scripts/dev/host_slots.py build -- \
  "$HOME/turku3d/tools/voice/bench/kokoro/bin/python" tools/video/voice.py narrate \
  --voice bf_emma --story tools/video/showcase-story.json
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/showcase-compose.py render
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/showcase-review.py
```

`showcase-compose.py render --scene game,rounds,generation` renders selected scenes;
`finish` mixes and exports, and `timeline` refreshes script, chapters and SRT.
Explicit shot lengths trim gameplay rather than accelerate it. Only generation is
time-lapsed; aerial shots use smooth capture camera curves. Never render the same
scene concurrently: overlay images have deterministic names. The four reviewers
check accuracy, visuals, audience pacing and audio. `showcase-review.py --quick`
checks final codecs, dimensions, preview size, timeline and subtitles without the
long frame/audio analysis. Full review saves 2-second contact sheets, freeze/black/
silence analysis, capture provenance and the exact final SHA-256. Preserve these
small reports when discarding intermediate videos and audio after approval.

Outputs: `turku3d-showcase-v3.mp4`, `turku3d-showcase-v3-preview-720p.mp4`, matching
`.srt`, `chapters-v3.txt`, `script-v3.md`, `thumbnail-v3.png`, and `review-v3.md` in
`~/turku3d/media/pipeline-video/`. A 720p preview uses 1.1 Mb/s video and 64 kb/s AAC,
with an enforced 25 MB maximum. No source imagery or captured frames are committed.

## Rebuild v2

Run from the `media-pipeline-video-v2` checkout. Dependencies: repository pipeline
environment, FFmpeg, Node, Chrome, and the local Kokoro and Whisper models installed
by the project's voice tooling. Raw inputs are read-only; no prefetch downloads run.

```sh
source ../sandbox.env
PYTHONPATH=textures python3 -m turkutex.codex.usage  # stop at 80% weekly usage
python3 tools/video/prepare.py
npm ci --prefix game --no-audit --no-fund
python3 scripts/dev/host_slots.py build -- node tools/video/build.mjs
```

`build.mjs` builds the unmodified production game plus a capture-only entry, sharing
the same three.js module. `runtime.ts` uses the production `CarVisual`, Rapier worker,
and `MusicSystem`; no gameplay source or runtime interface is changed. The car
controller follows mapped waypoints with steering and throttle, using real collision
and fixed 1/30-second steps. Camera shots use eased Catmull–Rom splines.

Start each server in a separate terminal, after sourcing the sandbox environment:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python tools/video/coarse_service.py
```

```sh
source ../sandbox.env
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" \
  --port "$((TURKU3D_PORT_BASE+1))" \
  --mount "/tiles=$TURKU3D_WORK/dist/tiles" --mount "/draft=$TURKU3D_WORK/coarse"
```

Then prepare the adjacent drafts, source visuals, narration and timing:

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/drafts.py
bash pipeline/env/run.sh python tools/video/sources.py
PYTHONPATH=textures bash pipeline/env/run.sh python tools/video/facade.py
HF_HOME="$HOME/turku3d/tools/voice/hf" HF_HUB_OFFLINE=1 \
  python3 scripts/dev/host_slots.py build -- \
  "$HOME/turku3d/tools/voice/bench/kokoro/bin/python" tools/video/voice.py narrate --voice bf_emma
bash pipeline/env/run.sh python tools/video/compose.py timeline
```

`drafts.py` requests a contiguous 5×5 neighbourhood around the core and reuses cloned
published L0s. Failed drafts are recorded in `$TURKU3D_WORK/video/drafts.json`; report
each failure to the orchestrator and frame around the missing cells. It writes a
capture tileset alongside the copied core assets so ground-texture URLs resolve
correctly. It never changes a shared tile or source input.

```sh
python3 tools/video/browser.py node tools/video/capture.mjs probe
bash pipeline/env/run.sh python tools/video/contact.py
python3 tools/video/browser.py node tools/video/capture.mjs music facade_open
python3 tools/video/browser.py node tools/video/capture.mjs capture
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/compose.py render
```

All commands over 30 seconds must be polled at 30 seconds, or wrapped individually
with `scripts/dev/codex-task wait-cmd --`. Do not run concurrent `wait-cmd` invocations:
its default log path is shared by calls within a task.

`browser.py` invokes the unchanged host slot scheduler, retaining one Chrome worker
and the shared debit ledger. It supplies this brief's explicit 150-minute allowance
because the runner only parses `BROWSER_BUDGET_MIN = 150`, not prose. The adapter is
restricted to this task's sandbox. Capture rejects requests to every origin except
its own dist port. `capture.mjs probe <shot,shot>` and `capture.mjs capture <shot,shot>`
allow selective framing and capture. Per-shot JSON records renderer, camera poses,
vehicle states and page errors. No shot is reused, and no final-frame hold is added.
The office marker is projected through the actual game camera. The draft/core
comparison uses contiguous halves of the same camera spline with different tile
inputs, and is explicitly labelled as matched captures rather than a live swap.

`compose.py render --scene <id,id>` re-encodes selected scenes; `compose.py finish`
remixes and rebuilds the final files. Titles fade after four seconds. Source panels
leave most of the moving game frame visible. The original Harbour Glass track is
rendered offline by the game's music engine, normalized quietly and sidechain-ducked
under narration; the final mix is normalized around −14 LUFS with a −1.5 dBTP target.
Subtitles follow Kokoro's synthesized token durations and prefer phrase boundaries.
The city name has an explicit Finnish phoneme override; the full street address
remains on screen while narration describes its riverbank location in English.
`compose.py thumbnail` refreshes only the thumbnail, from the clear aerial portion
of the closing game shot, with the same attribution strip as the video.

## Review and delivery

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/review.py
python3 scripts/dev/host_slots.py build -- \
  "$HOME/turku3d/tools/voice/bench/eval/bin/python" tools/video/transcribe.py
scripts/dev/gate --fast --base main
```

`review.py` checks delivery codecs, subtitles, black frames, silence, loudness and
motion, and creates `v2-contact-*.jpg` sheets every two seconds. It records the
exact input path and SHA-256 and rejects a duration mismatch with the timeline.
Independent accuracy,
picture/editing, sound/narration and audience reviewers must inspect the full cut,
the transcript and the evidence. Default to not ready when unsure, fix major issues,
and repeat until all pass or four rounds. A frame/transcript and ASR review cannot
substitute for perceptual listening; record that limitation if listening is unavailable.

Deliver `turku3d-pipeline-v2.mp4`, `turku3d-pipeline-v2.srt`, `chapters-v2.txt`,
`thumbnail-v2.png`, `script-v2.md`, `review-log-v2.md`, and
`turku3d-pipeline-v2-preview-720p.mp4` (under 25 MB). Preserve the v1 deliverables.
After final review, remove only this task's `video/audio`, `video/assets`, `video/edit`
and `video/shots` intermediates. Keep lightweight provenance, timeline, review metrics
and contact sheets. Intermediate storage must remain below 20 GB.
