# W1-12: End-to-end playtest suite and the "play it" entry point (Phase 1 task 1.16)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `game/tests/e2e*` and the developer entry points.

## Goal

One command gives the user a playable build, and a fast automated playtest proves it works before they try it.

## Deliverables

1. **`make play`**:
   - Builds or reuses the AOI tiles and the game, serves them on the main checkout's port block, and prints the URL.
   - Spawns the player in front of Läntinen Rantakatu 15, with a car parked nearby.
   - A start screen shows the gamepad status ("Xbox Wireless Controller connected") and the bindings with Xbox glyphs.
   - Takes about 5 s when everything is cached.
2. **Scripted playtest routes** on the real AOI tiles, on both backends, with a virtual gamepad:
   - Walk around the block over curbs.
   - Climb and descend a stair flight.
   - Try to walk into 5 buildings and get blocked.
   - Enter the car, drive the block, hit a curb, try to drive through a building and into the quay wall.
   - Assert positions, heights, speeds and no falling through the ground.
   - Record a short video (Playwright video) and screenshots for the user.
3. **Tagging and selection**: tag the specs by subsystem so `scripts/dev/gate` runs only the relevant ones. The full
   playtest runs in `make playtest`. Keep it fast by running specs in parallel per backend with separate port blocks
   from the sandbox.
4. **Playtest report**: one HTML page with the route videos, screenshots, perf numbers (from W1-08's `__perf` once it
   exists) and pass/fail. This is what the user looks at to decide whether the project is promising.

## Acceptance

- `make play` works from a clean main checkout once the Wave 1 layers land. Until then it works on the stub/AOI build.
- `make playtest` is green and its report and video are attached in the result.
