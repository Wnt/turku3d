# Game audio v1

`GameAudio` owns the Web Audio graph: buffer source → per-voice gain → optional HRTF panner →
channel gain → master gain → compressor → output. The renderer backend has no audio dependency.
The 22 original CC0 mono Opus effects total 255,691 bytes; the gull calls and the music are documented with them in
[`public/assets/audio/LICENSES.md`](../../public/assets/audio/LICENSES.md).

Gameplay emits typed `AudioEvent` objects from `mix.ts`. `GameplayAudio.update` adapts the existing
character/vehicle snapshots once per physics tick. The main loop only supplies snapshots, camera pose,
input state and mute actions. No gameplay class creates audio nodes or selects sound filenames.

- Footsteps use actual grounded travel distance, with walking/running strides, and the generated
  material table's `audio` key. Asphalt, stone/granite, grass, gravel and wood have separate samples.
  Soil uses grass; sand uses gravel; concrete/metal use stone; water/none are silent.
- The positional engine follows throttle and estimated RPM. Vehicle v1 has no simulated drivetrain:
  `engineRpm` gives the car a three-gear speed approximation, the scooter high CVT revs that climb with
  speed, and the trophy truck four long gears from an 800 rpm idle. `ENGINE_SOUND` picks the loop per vehicle class
  (`engine` for the car, the buzzing two-stroke `scooter` for the scooter at a higher playback rate, the V6 `truck`
  for the trophy truck, the loudest layer); the other engine loops stay silent.
- Tyres are synthesised at runtime (`engine/tyre.ts`): squeal, scrub, rolling noise and gravel grit, no samples.
  `TyreTracker` reads the wheels' sliding speed (lateral and longitudinal slip, faded in from 70 % of the tyre-mark
  limit), each tyre's load (suspension compression against its slow mean) and the surface, once per physics tick; a
  slide sounds after 80 ms of settled contact and 50 ms of sliding, so landings and bumps stay silent and a chirp is
  heard. `TyreSynth` is a stick-slip model of five tread blocks, calibrated against CC0 recordings: the tone cluster
  sits near 1 kHz with harmonics, falls with load and with the slide's speed, and loose surfaces only crunch.
  `TyreVoices` runs one AudioWorklet voice for the driven car or scooter and up to three for the nearest audible
  cars and scooters of other players (within 70 m, from the synced wheels their tyre marks use); the trophy truck's
  tyres render inside its engine voice (`ChassisSynth`), local and remote. `snapshot().tyres` lists the voices.
- Curb events consume W1-07 `curbStrike.id` once. Collision events use optional
  `VehicleState.impact = {id, impulse}` (N·s): the chassis momentum each fixed world step changes beyond gravity
  (vehicle/README.md "Collision telemetry"). A 0.5 m/s speed-change threshold and 150 ms cooldown keep resting contact quiet.
- Entering and leaving a car open the door, then close it after 300 ms; the scooter has no door sounds. Gamepad connection emits one blip.
- City bed, wind and distant traffic mix with a positional river bed. The river sounds from the nearest point of
  the Aura's centreline (`gulls.ts` `WATERS`, simplified from OpenStreetMap, from the Halinen rapids to the mouth)
  and fades to zero over 140 m. A city without a river line has no river bed.
- Gulls (`gulls.ts`) call now and then, only by the river bank (within 130 m of the Aura's centreline, full within
  60 m) and in the harbour (over the basin Linnanaukko and up to 350 m from its edge, full within 150 m: the quays
  and the ferry terminals); elsewhere and in other cities never. `GullScheduler`: the first call comes 10-30 s after arriving at the water, the next 45-120 s after the
  previous one ended (x 0.7 in the harbour, up to x 2 at the zone's edge), so about one call a minute or two and
  never two at once. Each call is a one-shot of one of two long calls (a herring gull and a common gull, mostly
  alternating, played 0.95-1.05 x), placed over the water 40-260 m from the listener and 12-35 m up. `GullVoices`
  gives it distance: an inverse-distance HRTF panner at the bird (reference 30 m), an air-absorption low-pass
  (about 10 kHz at 50 m, 3 kHz at 300 m), a reflection off the water a few milliseconds late, and an echo from the
  quays (a generated impulse response) whose share grows with distance. The two files are fetched when the
  listener first comes within 250 m of the zone, never at startup: in the start menu and while the world loads
  the gulls wait (`setLoading`, from the play session). Ambient and local to each client, so it needs no
  multiplayer sync. `snapshot().gulls` reports presence, the next call's wait and the last call's placement.
- D-pad down/K toggles master mute; N selects the next music track. Menu/Escape pauses (View/M is the map).
  The Audio panel has master, footsteps, vehicle, ambience, voice and UI sliders. The pause menu's Audio
  page has a music slider and mute button. Volumes and mute settings persist in `localStorage`.
  Pause fades loops and suppresses gameplay one-shots.

## Voice lines

`VoicePlayer` (`voice.ts`) loads `public/assets/voice/manifest.json`, written by the voice pipeline
([`audio/voice`](../../../audio/voice/README.md), `make voice-build`). It subscribes to a
`MissionHudSource` (`gameplay/contract.ts`) and plays the clip whose manifest `text` equals the message
that was just shown. Address announcements resolve to phrase, address, optional "Then", and second
address clips in the order shown in the HUD. Rules:

- A message change starts its clip; repeated states with the same message (timer ticks) do not restart it.
  Messages without a clip (HUD-only prompts, `"voiced": false` in `dialogue.json`) leave the current line alone.
- Lines never overlap. A new voiced line fades the previous out over 100 ms (`VOICE_CUT_SECONDS`) and
  starts when the fade ends. Of overlapping requests that are still loading, only the newest starts.
- Clips play on the `voice` bus (own volume slider). While a line plays, ambience and music pass through
  separate duckers at -6 dB (`VOICE_DUCK_DB`); their volume sliders remain independent.
- A line shown before the audio context runs waits for unlock, unless another message replaces it first.
- `playDialogLine(text, ended)` plays one opening-dialog clip and advances on its natural end;
  `stopDialogLine()` cancels it when the player advances or skips. Unvoiced messages leave it running.
- Startup clips are predecoded before Ready; other clips are fetched and decoded on first use and cached. Manifest or clip failures are contained in
  `voice.snapshot().errors`. `window.__game.voice` exposes `attach`, `show`, `narrate`, `snapshot`.

Startup creates the suspended context and predecodes every effect, the opening narration, and all initial
permit/pizzeria hint variants before Ready. The first key/pointer input resumes playback synchronously. Gamepad activity
also attempts unlock; Chrome may still require a click or key under its user-activation policy.
Asset loading is asynchronous and errors are contained in `audio.snapshot().errors`; missing audio
never blocks gameplay. No historical steps/impacts are replayed after loading. A pending connection
blip is retained. At most 24 active voices (including eight loops) are allowed. One-shot completion
disconnects every node; `dispose()` stops voices and closes the context on pagehide. QA mode stays silent.

The listener follows camera position, forward and rotated up vectors. Spatial positions use engine
world-local metres. Gain, pitch and listener/emitter changes use 40 ms smoothing. The master limiter
reduces clipping when effects overlap. Synthesized assets are a functional v1; field-recorded realism
and physical Elite controller listening remain later acceptance work.

## Verification

From the repository root, with sandbox environment loaded:

```sh
source ../sandbox.env
npm test --prefix game -- --run tests/unit/audio.test.ts tests/unit/voice.test.ts tests/unit/input.test.ts tests/unit/vehicle.test.ts
npm run build --prefix game
(cd game && npx playwright test tests/e2e/audio.spec.ts tests/e2e/voice.spec.ts)
GATE_PYTHON="$HOME/turku3d/envs/pipeline/bin/python" scripts/dev/gate --base main
```

Fake AudioContext tests cover lazy unlock, routing, failures, teardown, mute/volumes, bounded voices,
material mappings and mixing. Synthetic Rapier tests cover resting contacts and wall impact impulses.
The Chrome e2e test unlocks via a real key, decodes every asset, builds all eight loops, walks for
footsteps and triggers each effect family without errors on WebGPU and WebGL2. `window.__game.audio`
exposes `emit`, `snapshot`, `unlock` and `dispose` for diagnostics; `snapshot()` reports graph state,
loaded count, loop/voice counts, volumes, played counts and errors.

## Procedural music in play

The five-track engine starts on the first audio gesture using the same `AudioContext` as effects and voice.
The start screen, pause and save/load pages request `menu`; free play requests `explore`; an accepted order
requests `mission` until hand-over. Delivery progress is elapsed time divided by par time, updated once a
second. The intro and shop dialogue fully muffle music; driving applies a 0.6 muffle without reducing the
vehicle engine bus. The engine and its track clock survive a Vite play-session swap. N and the localhost
Developer page select the next track; free play also moves on every 64 bars and after each delivery. `music/params.json` holds the tuned defaults and accepts the lab's
Copy as JSON object. `window.__game.audio.snapshot().music` exposes the requested scene, progress, muffle
and the engine's playing snapshot for diagnostics. See [`music/README.md`](music/README.md).

## Held vehicle horns

`horn.ts` synthesises original diaphragm waveforms (car 350/440 Hz; truck 220/275 Hz, louder;
scooter 880 Hz). `GameAudio` owns the voices: oscillators → 25 ms attack / 60 ms release gain →
HRTF panner → vehicle bus → master limiter. A stable emitter id keeps a held horn continuous;
release, stale remote snapshots, pause and teardown stop it. No sample asset or licence is needed.
The shared capture harness can render the same synthesiser in `OfflineAudioContext` for peak, RMS,
release-tail and frequency checks.
