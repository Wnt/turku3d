# Turku3D pipeline video

Rebuilds the 1920×1080, 30 fps explainer from local data and a production game build. The output is outside the repository, under `~/turku3d/media/pipeline-video/`. No street-level photo is opened by the source, edit or capture scripts. `office-facade-description.json` is a previously generated *parameter description*, with no reference pixels. `story.json` names the evidence for every spoken section.

Requires the repository's pipeline environment, `ffmpeg`, Node, Playwright/Chrome, and the local Kokoro model installed by `make voice-setup`. Use the task sandbox's own port reservation and browser profile. The project's browser budget for this brief is 120 minutes. The camera capture uses only the QA renderer, fixed 1/30-second steps and scripted poses, never a gamepad or shared game server. Draft production uses the real producer with local prepared inputs and disables raw-data prefetch; the two requested tiles must already have source inputs.

From `repo/` in the `media-pipeline-video` sandbox:

```sh
source ../sandbox.env
python3 tools/video/prepare.py
npm ci --prefix game --no-audit --no-fund
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- npm run build --prefix game
# Start in a separate terminal; leave it running for the next command:
source ../sandbox.env
bash pipeline/env/run.sh python tools/video/coarse_service.py
# First terminal (after the service is listening):
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/video/drafts.py
# Start the isolated dist server in a separate terminal:
source ../sandbox.env
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/build/game" --port "$((TURKU3D_PORT_BASE+1))" --mount "/tiles=$TURKU3D_WORK/dist/tiles" --mount "/draft=$TURKU3D_WORK/coarse"
# First terminal, with the server ready:
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- node tools/video/capture.mjs capture
scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/video/sources.py
PYTHONPATH=textures bash pipeline/env/run.sh python tools/video/facade.py
HF_HOME="$HOME/turku3d/tools/voice/hf" HF_HUB_OFFLINE=1 scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- "$HOME/turku3d/tools/voice/bench/kokoro/bin/python" tools/video/voice.py audition
HF_HOME="$HOME/turku3d/tools/voice/hf" HF_HUB_OFFLINE=1 scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- "$HOME/turku3d/tools/voice/bench/kokoro/bin/python" tools/video/voice.py narrate --voice bf_emma
scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/video/compose.py render
```

`capture.mjs probe` renders one PNG per scripted shot to inspect framing before a full capture. Pass an optional shot id as its third argument. The capture uses headless Chrome's WebGPU backend at 1920×1080. `compose.py cards` renders a still of each chapter card; `compose.py timeline` generates the script, subtitles and chapters without encoding. Run `scripts/dev/codex-task wait-cmd -- bash pipeline/env/run.sh python tools/video/review.py` to generate frame contact sheets every two seconds, an `ffmpeg -af ebur128` report, subtitle bounds, codec checks, black-frame and silence scans. An independent reviewer should run local Whisper on the finished narration, inspect those artifacts and check every statement against the listed sources. Keep `review-log.md` with round results. No uploads are part of this workflow.

For a revised chapter card, run `compose.py render --scene <id>` with a comma-separated list of story IDs, then `compose.py finish` and `review.py`. The production voice is `bf_emma`; the task runtime could compare all 14 installed English packs objectively but had no audio-listening input, so a human listening pass remains required before public posting.

The final deliverables are `turku3d-pipeline.mp4`, `turku3d-pipeline.srt`, `chapters.txt`, `thumbnail.png`, `script.md`, and `review-log.md`. The edit is H.264 High video plus 48 kHz AAC audio, with the voice normalised near −14 LUFS. This pipeline keeps transient frames in pipes and only shot/chapter intermediates on disk. Remove `$TURKU3D_WORK/video/{audio,assets,edit,shots}` after final QA to stay below the 20 GB intermediate cap.
