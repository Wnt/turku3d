# Playtest coverage

`make playtest` prepares the AOI/game, runs both Chrome backends, and writes
`$TURKU3D_WORK/reports/playtest/index.html`. Open that file directly: it links relative
WebM videos, PNG screenshots, JSON measurements and failure traces. Earlier runs move to
`reports/playtest-history/<commit>-<timestamp>/` on the next invocation. Reports identify
uncommitted changes, selected tags and the actual renderer backend.

The current branch ships a **terrain/controller baseline**, not completed Phase 1
acceptance. The report always calls out that distinction. No missing-layer route is
silently skipped or counted as passing.

| Spec | Data | Assertions | Tags |
|---|---|---|---|
| Start screen | Synthetic dev tiles | Xbox connected/disconnected/reconnected, glyphs, A to start, office spawn and nearby car | `@launch @input @synthetic` |
| Audio startup | Synthetic dev tiles | Start dialog dismissal, audio unlock, bank decode, mute, footsteps and spatial effects | `@audio @input` |
| Four-seam loop | Real-data AOI | 48 m loop, four distinct L0 tiles, final position, speed bounds, continuous finite positions, sampled ground clearance | `@ground @streaming @character @aoi` |
| Office drive | Real-data AOI | Y enter, RT drives 15 m north, speed/lateral bounds, RB stops, Y exit, ground clearance | `@vehicle @ground @input @aoi` |
| Existing Xbox controls | Synthetic dev tiles | Analog walk, sprint, 12 cm curb, jump, camera, pause, hot-plug | `@input @character @ground @synthetic` |
| Existing stair route | Synthetic dev tiles | Ascent/descent, measured heights, locomotion and tread IK | `@character @structures @synthetic` |
| Existing car controls | Synthetic dev tiles | Enter, throttle, brake/reverse, exit | `@input @vehicle @synthetic` |

The virtual pad overrides **only** `navigator.getGamepads`. It does not teleport actors,
advance physics manually or overwrite state. All movement uses the production input path.
Chrome focus emulation isolates every virtual-pad page from other browser windows on the shared host.
Route evidence records every rendered frame and casts a ground ray in the physics worker
at approximately 10 Hz. The AOI tests reject missed rays and negative ground clearance.

## Gate selection

From the repository root, after exporting the sandbox environment:

```sh
make playtest-test                         # typecheck + offline cache/report tests
make game-test                             # existing game unit tests (~2 s)
make playtest PLAYTEST_ARGS='--grep @launch' # focused start-screen check, both backends
make playtest PLAYTEST_ARGS='--grep @vehicle'
make playtest PLAYTEST_ARGS='--grep @structures'
make playtest PLAYTEST_ARGS='--grep @aoi'
make playtest                              # all available routes and videos
```

`scripts/dev/gate.toml` connects input/UI to `@launch|@input`, actors/physics to
`@walk|@drive|@character|@ground|@structures`, vehicle to `@drive|@vehicle`,
and tile streaming to `@streaming`. Unmapped browser changes run the full synthetic suite.
The gate runs the start-screen and controller routes on synthetic tiles and the cache/report
unit tests. The full AOI video run remains `make playtest`. Two workers share the GPU;
backend servers use `PORT_BASE + 8` and `PORT_BASE + 9`, with no server reuse and isolated
Playwright profiles under `TURKU3D_BROWSER_DIR`. Videos are 640×360 at 12 fps; screenshots
remain 1280×720.
Never overlap two invocations that use the same ports/report directory.

## Remaining acceptance work

After W1-01/02/03/04 land, survey and commit routes on the assembled geometry:

- Walk the street loop around the office and cross its measured curbs.
- Ascend and descend a real stair flight, with measured bottom/top heights.
- Approach five distinct building shells and assert the character remains outside.
- Drive the street loop, measure the curb-strike speed/pitch response, and strike a
  building and the quay wall without penetration or falling.

The Phase 0 AOI is an orthophoto over a coarse terrain mesh, including interpolated water;
its textures are not building colliders. The synthetic stair route cannot certify the AOI.
Do not remove the report's real-AOI follow-up notice until these routes pass on both backends.

The report captures `window.__perf` when available (value or zero-argument snapshot function).
If render telemetry is unavailable it reports the game's frame-time EMA distribution, marked diagnostic.
1280×720 automated video results are not evidence for the 1440p/60 fps acceptance target.
