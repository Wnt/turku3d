# Procedural music engine

`MusicSystem` takes an existing `AudioContext` and optional destination `AudioNode`. Call
`start()` synchronously in an input handler. It calls `resume()` there, then schedules
notes with a 120 ms lookahead. Use `setState('explore' | 'mission' | 'menu')`,
`setDeadline(progress)` with progress 0–1, `setMuffled(amount)`, `setVolume(value)`,
`nextTrack()` or `selectTrack(index)`. `deliver()` plays a resolved cadence, resets the
deadline and requests explore. `snapshot()` reports bar, beat, BPM and layers;
`analyser` exposes the output for the tuning app. Call `dispose()` on teardown.

Changes of state or track begin on the next bar. The outgoing and incoming variants
crossfade over `crossfadeBars`; the mission entry has a short chime. Suspended contexts
drop stale scheduler time, avoiding notes in the past. Every note has an attack and
release envelope; the engine needs no audio assets or runtime network calls.

| Track | BPM | Key | Style |
|---|---:|---|---|
| Aura Neon | 108 | A minor | night synthwave |
| Market Sun | 102 | C major | acoustic daytime |
| River Dub | 92 | D minor | dub bass |
| Ring Road | 120 | E minor | motorik rock |
| Harbour Glass | 84 | G major | minimal piano ambient |

`params.json` is the `DEFAULT_PARAMETERS` source and accepts the lab's Copy as JSON output.
It contains volume 0.42, crossfadeBars 2, muffleCutoff 850 Hz,
muffleAmount 0.7, muffleMs 300, hurryStart 0.65, tempoLift 0.18,
stingerLeadBars 2, and layerGains pad 0.6, bass 0.62, drums 0.5, lead 0.52,
percussion 0.4, panic 0.55. `setParameters` accepts a partial copy. Each track may
set `liftStart`, `tempoLift` and `panicStem`; user tuning shifts the first two relative
to the defaults. The hurry curve smoothsteps deadline progress from the lift start
to 1; it raises tempo, percussion, high frequencies and an ending chime.

To add a track, append a `Track` to `tracks.ts` with a distinct id, name, style, BPM,
key, root frequency, four chord offsets and oscillator waveform. Both variants use
the same tempo/key/pattern, with different layer density and gains.
