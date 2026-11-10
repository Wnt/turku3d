# Music

`MusicSystem` streams six instrumental tracks (`public/assets/audio/music/<id>.ogg`, Ogg/Opus 96 kbit/s stereo, about
1.5 MB and two minutes each; sources and licences in `public/assets/audio/LICENSES.md`). It takes an existing
`AudioContext` and an optional destination `AudioNode`. Call `start()` synchronously in an input handler. Then
`setState('explore' | 'mission' | 'menu')`, `setDeadline(progress)` with progress 0–1, `setMuffled(amount)`,
`setVolume(value)`, `nextTrack()` or `selectTrack(index)`; `deliver()` plays the resolved cadence and returns to free
play on its next track. `snapshot()` reports the state, the playing track, its position and rate, the deadline
intensity, the load guards and which streams have been opened. Call `dispose()` on teardown.

| Track | File | BPM | Style | Plays |
|---|---|---:|---|---|
| Harbour Glass | `menu.ogg` | 70 | piano and strings | the menu theme (start menu, pause menu), looped |
| Aura Neon | `synthwave.ogg` | 108 | night-drive synthwave | free play 1 |
| Market Sun | `lofi.ogg` | 80 | lo-fi hip hop | free play 2 |
| Ring Road | `funk.ogg` | 112 | 1970s cop-show funk | free play 3; second track of a delivery run |
| River Dub | `dubtechno.ogg` | 120 | dub techno | free play 4 |
| Last Slice | `dnb.ogg` | 172 | liquid drum and bass | a delivery run, from the top with the mission bell |

`tracks.ts` holds the list and `PLAYLISTS`. Free play moves to its next track when one ends, on N and after each
delivery; pausing crossfades to the menu theme and back to where free play was.

**Streaming, never preloading.** Each track is a media element created when the track is first played
(`preload = 'none'`), so no music is requested before it is wanted. A change of track is applied once the request
has stood for `SETTLE` (0.6 s): a quick pause and resume starts nothing. `setHold(true)` (the world is loading)
starts no stream; `setLobby(true)` (the start menu) waits `LOBBY_SETTLE` (4 s) with audio on before the menu theme
starts, so clicking straight through the menu downloads no music. `GameAudio.setLoading` drives both from the play
session and holds for fifteen more seconds after the game becomes playable (`ENTRY_QUIET`). With the music muted or at zero volume the
stream fades out and pauses.

**The delivery deadline** (`hurry`): from `hurryStart` (65 % of the par time) the track speeds up smoothly to
`1 + rateLift` (6 %) at the deadline with its pitch preserved, the low-pass opens from 12 to 18 kHz and the level
rises by a quarter; from `stingerFrom` (82 %) a two-note stinger sounds every two seconds. A run longer than the
chase track continues with the funk at the same lift.

Tracks crossfade over `crossfadeSeconds` (2 s) through per-track gains into one master gain (the volume) and a
low-pass (`setMuffled`: intro and shop dialogue fully, driving 0.6). A stream that fails is skipped after five
seconds; a refused start is retried. The bell, the stinger and the cadence are sine chimes. `params.json` is the
`DEFAULT_PARAMETERS` source; `setParameters` accepts a partial copy.
