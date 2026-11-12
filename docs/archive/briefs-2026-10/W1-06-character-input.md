# W1-06: Input system with Xbox gamepad, and GTA V-style character (Phase 1 task 1.13 + gamepad)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `game/src/input/` (new) and the player/character code.

## Goal

The user plays with an **Xbox Elite Series 2**, which Chrome on macOS reports as "Xbox Wireless Controller", standard
mapping. It already works on https://anygamepad.github.io/. Walking must feel like GTA V: smooth analog movement,
camera on the right stick, and stairs and curbs that look right with feet placed on treads.

## Deliverables

1. **Input module** `game/src/input/`:
   - Named actions (move x/y, look x/y, sprint, jump, interact/enter-vehicle, throttle, brake, steer, handbrake,
     camera reset, pause) from the Gamepad API standard mapping, with a radial dead zone and response curves.
   - Hot-plug support and per-action rebinding data.
   - Keyboard and mouse fallback through the same actions.
   - Vibration through `vibrationActuator` dual-rumble where Chrome supports it. Trigger rumble is optional;
     feature-detect it.
   - Xbox glyphs in the HUD prompts.
   - This module is the **only** way gameplay reads input. Migrate the existing keyboard code and delete the old
     paths.
2. **GTA V-like bindings**:
   - On foot: left stick = move (analog walk to run), A = sprint (tap or hold), X = jump, Y = enter vehicle, right
     stick = camera, R3 = camera reset.
   - In the car: RT = throttle, LT = brake/reverse, left stick = steer, RB = handbrake, Y = exit.
   - Document the bindings in `game/README.md`.
3. **Character**:
   - Analog speed blending (walk, jog, sprint).
   - A stair locomotion state when on `stair_slope`.
   - **Foot IK** that raycasts against `stair_tread_helper` and the ground, with pelvis offset, so feet land on treads
     and on both sides of curbs.
   - Smooth step-up on 7–15 cm curbs (autostep with visual smoothing of the capsule-to-mesh offset).
   - Interpolation between 60 Hz physics states, so there is no judder at high refresh rates.
   - CC0 animations: extend the existing Quaternius/UAL set, and add stair up/down clips if a CC0 source has them.
     Otherwise use procedural adjustments plus IK.
4. **Tests**:
   - Unit tests for the dead zone, curves and mapping, using a fake Gamepad object.
   - Playwright tests that inject a virtual gamepad (override `navigator.getGamepads`) and walk a route over a curb and
     up stairs in the synthetic dev tiles on both backends, asserting position and height.

## Acceptance

- With the real controller, the user can walk, run, jump, look around and enter and exit the car. Verify on the
  synthetic dev tiles with the virtual-gamepad e2e test.
- Character criteria from ARCHITECTURE 1.13: steps up a 7–15 cm curb with no hitch, walks every stair flight with
  feet on the treads, and is blocked by walls of 0.35 m or more.
- No gameplay code reads `navigator.getGamepads` or `KeyboardEvent` outside `game/src/input/`. The gate's grep or a
  test checks this.
