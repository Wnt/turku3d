# Input

`Input` (`index.ts`) is the only owner of browser input. Gameplay reads actions (`input.actions`,
`input.consume(action)` for press edges, `lookX` / `lookY` / `zoom` per frame) and never keys, pointers or
device indices; `tests/unit/input.test.ts` fails if code outside `src/input/` listens to them.

| File | Role |
|---|---|
| `bindings.ts` | Actions, serializable default bindings (keyboard codes, Standard Gamepad buttons and axes), the radial stick curve, `mapActions` and `glyph` |
| `index.ts` | `Input`: listeners, per-frame merge of keyboard, gamepad and touch, edges, sprint burst, rumble |
| `touch.ts` | Touch source: `TouchPad` (pointer bookkeeping, no DOM) and `TouchOverlay` (thumb buttons and the floating stick) |
| `haptics.ts` | `Haptics`: controller rumble from what the player feels; pure mappers (impulse, step, speed -> strength and duration) |

## Sources

`input.source` is `'keyboard'`, `'gamepad'` or `'touch'`: the device the player used last. Prompts
(`input.prompt(action)`) show gamepad glyphs for gamepad and touch, key names for the keyboard. Per action
the frame takes the largest magnitude of the three sources.

## Safe reset

`respawn` (Xbox Y, keyboard R, touch Reset) returns a lost player to solid ground; `vehicleReset` (Xbox B / R) does the
same in a car. While lost either action recovers the player (gameplay/recovery.ts); Y is `interact` otherwise, and
while lost `interact` is swallowed so Y never enters or leaves a car. Outside the lost state B / R flips the driven car upright in place, or a flipped car within 4 m on foot.
The recovery notice appears after a second of stillness on the side or roof and clears each frame when no longer relevant.

F10 toggles desktop fullscreen synchronously from the keyboard gesture; it has no gameplay action.
The HUD button uses the same toggle. Esc exits fullscreen through the browser; fullscreenchange updates the button.

## Fly mode

`flyUp` (Xbox X, RT, Space) and `flyDown` (Xbox B, LT, Q) are held values read only while the developer fly mode
is on (localhost only, `gameplay/flyMode.ts`, `ui/README.md`); a double tap of `jump` toggles it.

## Developer panels

`timePanel` (T) shows the developer time panel (`ui/timePanel.ts`); `timePause` (0), `timeSpeed1`–`timeSpeed4` (1–4),
`timeHourBack` / `timeHourForward` (BracketLeft / BracketRight: `[` `]`, Å ¨ on a Finnish keyboard),
`timeMinutesBack` / `timeMinutesForward` (Comma / Period) and `timeLive` (B) act only while it shows; the gamepad
reaches the same controls through Developer → Time of day. A focused control in `#dev-layers` or `#time-panel` keeps
its own keys (a slider's arrows): only the panels' keys reach the actions there, and a mouse drag that starts in a
panel never turns the camera.

## Haptics

`play.ts` feeds `Haptics.update` once per render frame (driven vehicle state and input, or the character's
grounded flag and vertical speed) and `Haptics.footstep` from the audio stride events; every effect goes to
`Input.rumble` as one `dual-rumble` (strong = low-frequency motor, weak = high-frequency motor). Nothing rumbles
while paused, in a menu or on the start screen, and a vehicle's old events never replay when it is entered.
`__game.haptics` exposes `counts` per cause and a `log` of the latest transients.

Strength ladder, weakest first (`haptics.ts` holds the constants):

| Cause | Trigger | Strong | Weak | ms |
|---|---|---|---|---|
| Engine (car) | RT (or LT reversing) above 5 %; strongest pulling away | 0 | up to 0.14 | 150, renewed every 100 |
| Engine (scooter) | always while ridden, rising with throttle and speed | 0 | 0.05–0.18 | 150, renewed every 100 |
| Stairs | each footstep on a `STAIR` material | 0.10 | 0.08 | 45 |
| Landing | touchdown after falling at 2 m/s or faster (a jump is 4.6 m/s) | 0.15–0.8 | 0.1–0.5 | 70–180 |
| Kerb | a wheel's ground path steps 7 cm or more (`VehicleState.bump.height`), or a curb-material strike | speed × height × 0.8 (drops × 0.7) | 0.6 × strong | 120 |
| Jolt | suspension compression at 1 m/s or faster (humps, potholes, landings) | 0.2–0.8 (at 4 m/s) | 0.12–0.62 | 70–150 |
| Collision | chassis speed change of 0.5 m/s or more (`impact.impulse / mass`) | 0.35–1 (at 8 m/s) | 0.5–1 | 120–350 |

Kerbs in the real tiles are lidar slopes without curb material (10–20 cm over about 0.6 m), so they rumble through
the wheel step (vehicle/README.md "Bumps") with the same strength the synthetic scene's curb faces give through
the curb-strike module: a 12 cm kerb at 4 m/s plays 0.38 strong either way. The engine uses only the weak motor so
every transient stays distinct. One transient plays at a time: a new one preempts a running one only when it is
stronger, and the engine pulses pause until it ends. Measured on the same 24 s car route (three kerb crossings at
3, 5 and 8 m/s, two cruises at 8 and 12 m/s, a building crash) the synthetic scene went from 15 to 35 transient
rumbles per minute and the real tiles from 0 to 67.5; the real tiles' rough paving is felt as well as the kerb.

## Touch

Touch is enabled when `navigator.maxTouchPoints > 0` or on the first touch pointer event; it then becomes
the active source until a key, a mouse click or a gamepad takes over, and back on the next touch.

- **Stick**: a touch on the left half of the canvas places a floating stick under the thumb. Its offset over
  `STICK_RADIUS` (64 px) goes through the gamepad's radial dead zone and curve (`MOVE_CURVE`) to `moveX` /
  `moveY` on foot and `steer` / `throttle` (up) / `brake` (down, reverse) in a vehicle. Beyond the radius the
  base follows the thumb. A second thumb on the left half is ignored.
- **Look**: a drag on the right half adds its pixels to the mouse-look accumulator (0.0025 rad/px, the same
  scale as the mouse). Two fingers on the right half pinch: `zoom` gets about one wheel notch per 10 % change
  of the finger distance.
- **Buttons** (`TOUCH_BUTTONS`, 64 px bottom right, 56 px top right, gamepad glyph plus a label): Use (`mission`,
  the A action that opens shops), Sprint (hold), Jump, Enter car / Exit car (`interact`), Handbrake, Map and
  Pause / Resume, Back (`menuBack`) while a shop menu is open, and Reset (`respawn`) with Map and Pause while the
  player is lost off the built area or in the river (`lost`, gameplay/README.md "Safe reset"). A button holds its action until lifted; a tap
  between two frames is still a press edge. `main.ts` calls `input.setTouchContext('foot' | 'car' | 'menu' |
  'paused' | 'lost' | 'hidden')` every frame to pick the visible set; the overlay is hidden unless the source is touch.
- **Multi-touch**: Pointer Events tracked by `pointerId`, so stick, look and buttons work at once. The canvas has
  `touch-action: none`; each touch is captured by the element it started on, so a finger that started on a
  button never selects a shop row under it.

Styles (`#touch-controls`, `.touch-*`) live in `game/index.html` and respect the safe-area insets.

## Tests

```sh
cd game && npx vitest run tests/unit/input.test.ts tests/unit/touch.test.ts tests/unit/haptics.test.ts
# Chrome, synthetic tiles, both backends (after make game-build):
cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test gamepad.spec.ts touch.spec.ts
```

The virtual pad of `tests/e2e/fixtures.ts` records every `playEffect` call in `__virtualPad.rumbles`. The real-tile
haptics routes (a surveyed lidar kerb and the building behind it by car, a stair flight down to the quay on foot)
are in `tests/e2e-playtest/aoi.spec.ts`, WebGPU only:

```sh
TILE_DIST="$TURKU3D_DATA/dist/tiles" make playtest PLAYTEST_ARGS='--project webgpu --grep @haptics'
```

`tests/e2e/touch.spec.ts` runs at 844×390 with `hasTouch` and `isMobile`, sends multi-finger touches through
CDP (`Input.dispatchTouchEvent`) and writes screenshots to `$TURKU3D_WORK/reports/gameplay-touch-controls/`.

`Input.activity` is a non-consuming per-update set of press starts (either axis sign, taps, buttons and mouse-look starts). Controls help samples it before gameplay consumes actions. Held input counts once until released.
