# W1-07: Car that feels curbs, with gamepad driving (Phase 1 task 1.14)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `game/src/vehicle*` / car code.

## Goal

Driving around the office block feels like GTA V: responsive arcade-sim handling with analog throttle and brake on
RT/LT. **Curbs are felt**: a pitch and roll jolt, speed loss and rumble when hitting them, and high curbs or walls
stop the car. Rapier's raycast vehicle climbs anything under 38 cm with no speed loss (verified), so a custom
curb-strike module is required.

## Deliverables

1. **Handling**:
   - Tune the Rapier `DynamicRayCastVehicleController`: suspension, friction curve, steering that depends on speed,
     a handbrake drift, and a reasonable top speed (~20–30 m/s).
   - A proper car model: a CC0 or CC-BY glTF car (Kenney, Quaternius or similar), with the license recorded,
     replacing the box. Wheel meshes follow the suspension.
2. **Curb-strike module**:
   - Wheel shape-casts against faces with the `CURB` flag or material `curb` / `quay`.
   - On a hit, apply an impulse proportional to speed × step height, a speed loss, and a chassis pitch/roll kick.
   - Above about 25 cm at low speed, block the car.
   - Trigger rumble through the input module.
   - Use the material table, not geometry guesses.
3. **Input**: use `game/src/input/` actions from W1-06 (throttle, brake, steer, handbrake, interact). If W1-06 has
   not landed yet, write against the action names in its brief and rebase when it lands. Do not create a second input
   path.
4. **Camera**: a chase camera with speed-dependent distance and FOV, the look-behind/orbit on the right stick, and
   collision with buildings.
5. **Validator hooks**: make the car module importable headless (Node) so W1-05's validator drives it. Add curb
   scenarios to the synthetic fixtures.

## Acceptance (ARCHITECTURE 1.14)

- Over a 7–15 cm curb at 3–10 m/s: chassis pitch spike ≥ 2° and speed loss 5–20 %.
- A step above 25 cm at under 3 m/s blocks the car.
- At 30 m/s the car never tunnels through a building.
- Tests: unit tests for the curb-strike maths, a headless Node physics test on the fixtures, and a Playwright drive
  with a virtual gamepad on both backends.
