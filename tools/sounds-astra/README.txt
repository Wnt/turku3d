Astra soundboard, 2026-11-10

Independent research and candidate audio. Do not merge this work into the game.
Source code and original synthesis/compositions: CC0-1.0.
Third-party recordings keep the licences and credits recorded in sources.json.
No recordings or model weights are committed. All output is under:
  ~/turku3d/loops/sounds-astra/

Dependencies: Python with numpy/scipy/matplotlib, FFmpeg with libopus;
Node with esbuild/playwright installed under work/node in the output directory.
The existing pipeline Python was used; no shared environment was changed.

From the sounds-astra worktree:
  node tools/sounds-astra/render-tyres.mjs
  python3 ~/turku3d/tools/host-lease/session.py run mac sounds-astra --kind func --slots 1 --headless --queue-timeout 60 -- node tools/sounds-astra/render-baselines.mjs
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/generate-effects.py
  OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/compose.py
  OPENBLAS_NUM_THREADS=1 ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/analyse.py
  ~/turku3d/envs/pipeline/bin/python tools/sounds-astra/build-board.py
  python3 ~/turku3d/tools/host-lease/session.py run mac sounds-astra --kind func --slots 1 --headless --queue-timeout 60 --load-timeout 120 --idle 180 -- node tools/sounds-astra/check-board.mjs
  bash ~/turku3d/loops/sounds/publish.sh astra ~/turku3d/loops/sounds-astra/board

Input provenance: sources.json includes public URL, author, licence, retrieval
date and the exact downloaded MP3 SHA-256. Source HTML snapshots stay in inputs/.
The source audio is intentionally not bundled with this code download.

Candidate loops use overlap/trim for ambience and tyres, circular note tails and
effects for composed music. The player uses decoded AudioBufferSourceNode loops,
not HTMLAudioElement loop timing. A short gain ramp protects manual play/stop.

Measurement limitations: average spectrum, gated RMS, endpoint jump and decode
success do not establish perceived quality. Ratings are provisional design
judgments, not a listener test. Recording species labels come from the recordist.
Common-gull source contains other birds. The tyre model is a perceptual prototype,
not a validated physical solver. No claim of a Turku field recording is made.
