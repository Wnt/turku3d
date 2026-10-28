# Turku3D pipeline video

The second cut is a 1920×1080, 30 fps explainer with moving game footage throughout,
28 unique camera paths, physics-driven car shots, compact source overlays, and local
Kokoro `bf_emma` narration. The first cut remains available in Git history at `bff2d9e3`.
Final files are outside the repository in `~/turku3d/media/pipeline-video/`.

No street-level photograph is opened or included. `facade.py` reads a previously
generated parameter description and renders its procedural output. City source
images and all game frames carry City of Turku / CC BY 4.0 and OpenStreetMap / ODbL
attributions. The thumbnail comes from a captured game frame. There are no uploads,
external multiplayer connections, host gamepads, or paid services.

## Rebuild

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

`compose.py render --scene <id,id>` re-encodes selected scenes; `compose.py finish`
remixes and rebuilds the final files. Titles fade after four seconds. Source panels
leave most of the moving game frame visible. The original Harbour Glass track is
rendered offline by the game's music engine, normalized quietly and sidechain-ducked
under narration; the final mix is normalized around −14 LUFS with a −1.5 dBTP target.

## Review and delivery

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/video/review.py
python3 scripts/dev/host_slots.py build -- \
  "$HOME/turku3d/tools/voice/bench/eval/bin/python" tools/video/transcribe.py
scripts/dev/gate --fast --base main
```

`review.py` checks delivery codecs, subtitles, black frames, silence, loudness and
motion, and creates contact sheets every two seconds. Independent accuracy,
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
