# Input

`Input` (`index.ts`) is the only owner of browser input. Gameplay reads actions (`input.actions`,
`input.consume(action)` for press edges, `lookX` / `lookY` / `zoom` per frame) and never keys, pointers or
device indices; `tests/unit/input.test.ts` fails if code outside `src/input/` listens to them.

| File | Role |
|---|---|
| `bindings.ts` | Actions, serializable default bindings (keyboard codes, Standard Gamepad buttons and axes), the radial stick curve, `mapActions` and `promptLabel` |
| `index.ts` | `Input`: listeners, per-frame merge of keyboard, gamepad and touch, edges, sprint burst, rumble |
| `touch.ts` | Touch source: `TouchPad` (pointer bookkeeping, no DOM) and `TouchOverlay` (thumb buttons and the floating stick) |
| `haptics.ts` | `Haptics`: controller rumble from what the player feels; pure mappers (impulse, step, speed -> strength and duration) |
| `actuator.ts` | Plays and stops a rumble on whatever vibration a pad exposes, by feature detection |

## Sources

`input.source` is `'keyboard'`, `'gamepad'` or `'touch'`: the device the player used last. Per action the frame
takes the largest magnitude of the three sources.

## Prompts

Every button, key and stick name in help, hints and "Press … to" prompts comes from `promptLabel(action, bindings,
device)` (`bindings.ts`), through `input.prompt(action[, device])`. `input.device` is the device used last:
`'playstation'` while a Sony pad (DualSense, DualShock 4: `playstationPad(id)`) is in use, `'xbox'` for every other
pad, else `'keyboard'` or `'touch'` (the overlay's buttons carry the Xbox glyphs). `input.padFamily` is the family of
the pad seen last (Xbox until one shows up); the start screen's controller list and the panel buttons use it.

| Device | Names |
|---|---|
| `playstation` | ✕ ○ □ △, L1 R1 L2 R2, L3 R3, Create, Options, PS, Touchpad, D-pad ↑ ↓ ← →, L stick, R stick |
| `xbox`, `touch` | Ⓐ Ⓑ ⓧ Ⓨ, LB RB LT RT, L3 R3, View, Menu, Xbox, D-pad ↑ ↓ ← →, Ⓛ Ⓡ |
| `keyboard` | key names (E, Shift, Space, Esc, ↑), axis pairs (A/D), WASD for moving, mouse for looking |

A pad without a binding for the action names its key (J copy position). `input.promptRevision` changes with the
device, the pad family, a rebind or a layer switch: per-frame UIs redraw on it, static labels use
`input.watchPrompts(listener)`. `tests/unit/prompt-labels.test.ts` fails if a button glyph appears outside `bindings.ts`
or a dialogue "Press" line does not take `{button}`.

## Safe reset

`respawn` (Xbox Y, keyboard R, touch Reset) returns a lost player to solid ground; `vehicleReset` (Xbox B / R) does the
same in a car. While lost either action recovers the player (gameplay/recovery.ts); Y is `interact` otherwise, and
while lost `interact` is swallowed so Y never enters or leaves a car. Outside the lost state B / R flips the driven car upright in place, or a flipped car within 4 m on foot.
The recovery notice appears after a second of stillness on the side or roof and clears each frame when no longer relevant.

F10 toggles desktop fullscreen synchronously from the keyboard gesture; it has no gameplay action.
The HUD button uses the same toggle. Esc exits fullscreen through the browser; fullscreenchange updates the button.

## Sprint in the trophy truck

`sprint` (Xbox A, Shift) has no driving use in a car; in the trophy truck each press cycles the rear-steer mode
(`vehicle/README.md` "Rear steer"). A is also `mission`: a press at a shop or drop-off goes to the mission
(`MissionSystem.interactable`), not to the rear steer. Shift is only `sprint`.

## Fly mode

`flyUp` (Xbox X, RT, Space) and `flyDown` (Xbox B, LT, Q) are held values read only while the developer fly mode
is on (localhost only, `gameplay/flyMode.ts`, `ui/README.md`); a double tap of `jump` toggles it.

## Editor layer

`input.layer = 'editor'` (set by the developer object editor, `dev/editor/README.md`, while its mode is on and no menu
is open) maps with `editorBindings`: the editor actions get ✕ ○ △ L1 R1 Create and the D-pad, which gameplay actions
lose; the sticks, □, the triggers, Options and R3 stay shared so walking, flying and pausing work, and L3 is the fast
modifier. `editToggle` (DualSense touchpad click 17, Xbox L3 + R3 as a `chord` binding, F8) is bound in both layers.
Prompts use PlayStation button names when the active pad is a Sony controller (`input.device`, see Prompts).

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
`Input.rumble` as one effect (strong = low-frequency motor, weak = high-frequency motor). Nothing rumbles
while paused, in a menu or on the start screen, and a vehicle's old events never replay when it is entered.
`__game.haptics` exposes `counts` per cause, a `log` of the latest transients, `surface` (this frame's surface
rumble with the wheel share and km/h) and `continuous` (the level last sent); `__game.input.rumbleDriver` names
how the pad in use vibrates.

Strength ladder, weakest first (`haptics.ts` holds the constants):

| Cause | Trigger | Strong | Weak | ms |
|---|---|---|---|---|
| Engine (car) | RT (or LT reversing) above 5 %; strongest pulling away | 0 | up to 0.14 | 250, renewed every 100 |
| Engine (scooter) | always while ridden, rising with throttle and speed | 0 | 0.05–0.18 | 250, renewed every 100 |
| Cobbles | any vehicle above 5 km/h with wheels on a `SURFACE_ROUGHNESS` material | 0.08–0.55 (at 50 km/h) × wheel share | 0.15–1 × wheel share | 250, renewed every 100 |
| Stairs | each footstep on a `STAIR` material | 0.10 | 0.08 | 45 |
| Landing | touchdown after falling at 2 m/s or faster (a jump is 4.6 m/s) | 0.15–0.8 | 0.1–0.5 | 70–180 |
| Kerb | a wheel's ground path steps 7 cm or more (`VehicleState.bump.height`), or a curb-material strike | speed × height × 0.8 (drops × 0.7) | 0.6 × strong | 120 |
| Jolt | suspension compression at 1 m/s or faster (humps, potholes, landings) | 0.2–0.8 (at 4 m/s) | 0.12–0.62 | 70–150 |
| Collision | chassis speed change of 0.5 m/s or more (`impact.impulse / mass`) | 0.35–1 (at 8 m/s) | 0.5–1 | 120–350 |

Kerbs in the real tiles are lidar slopes without curb material (10–20 cm over about 0.6 m), so they rumble through
the wheel step (vehicle/README.md "Bumps") with the same strength the synthetic scene's curb faces give through
the curb-strike module: a 12 cm kerb at 4 m/s plays 0.38 strong either way. The engine uses only the weak motor so
every transient stays distinct. One transient plays at a time: a new one preempts a running one only when it is
stronger, and the continuous pulses pause until it ends.

The continuous layer is the louder per motor of engine, horn and surface. Its 250 ms pulses are renewed every
100 ms, so they overlap down to a few frames per second; a transient plays with at least the layer's magnitudes
on each motor and hands back one frame before it ends, so the motors never stop in between. The moment the layer
ends (off the cobbles, at or below 5 km/h, out of the vehicle, paused, menu) a zero effect cuts the pulse still
playing; losing window focus resets the actuator.

Surface rumble (`surfaceRumble`, `roughShare`): level = 0.15 + 0.85 × (km/h − 5) / 45, clamped at 50 km/h, times
the share of wheels in contact with a rough material (one wheel of four in a cobbled gutter: a quarter; a wheel in
the air counts as off). Weak motor = level (the rapid buzz of the setts), strong motor = 0.55 × level (body), which
leaves kerbs (strong up to 1), jolts (0.8) and crashes (1) audible on top at full speed. `SURFACE_ROUGHNESS` is a
table per collision material, the only place deciding what rattles: `cobblestone` = 1. The generators give that
material to every sett and cobble surface in the source data (street register noppakivi, nupukivi, luonnonkivi,
kenttäkivi, graniitti; OSM `sett`, `cobblestone`, `unhewn_cobblestone`), Kauppatori and Portsa included;
`paving_slab` is concrete pavers, brick and slabs and stays silent.

### Controllers

`actuator.ts` detects the vibration interface, never the controller name, and `Input` drives the pad in use: the
pad with the latest input, else the one used before, else the first. A pad that takes over stops the one left
behind; a pad that disconnects mid-drive is silent and rumbles again within 100 ms of coming back, under any index.

| Interface | Detection | Driven as |
|---|---|---|
| `vibrationActuator.playEffect('dual-rumble')` | `effects` lists it (Chrome 126+), `canPlayEffectType` accepts it (Safari), `type` is `dual-rumble` (older Chrome), or neither is present | strong and weak magnitudes as they are; `reset()` stops |
| `hapticActuators[i].pulse(value, ms)` | no usable `vibrationActuator`, at least one actuator with `pulse` (Firefox) | two actuators: strong, weak; one: the larger magnitude; `pulse(0)` stops |
| none | neither | nothing |

Chrome exposes `dual-rumble` for Xbox pads (Elite Series 2 included; XInput, Bluetooth and USB), DualSense and
DualShock 4 (HID, USB and Bluetooth), Switch Pro and generic XInput or HID pads with rumble, so they all get the
same magnitudes: one curve, no per-family gain (none was measured to be needed; a gain keyed on the vendor id
belongs in `actuator.ts` if a pad feels weaker). Measured on the same 24 s car route (three kerb crossings at
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
