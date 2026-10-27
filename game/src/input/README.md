# Input

`Input` (`index.ts`) is the only owner of browser input. Gameplay reads actions (`input.actions`,
`input.consume(action)` for press edges, `lookX` / `lookY` / `zoom` per frame) and never keys, pointers or
device indices; `tests/unit/input.test.ts` fails if code outside `src/input/` listens to them.

| File | Role |
|---|---|
| `bindings.ts` | Actions, serializable default bindings (keyboard codes, Standard Gamepad buttons and axes), the radial stick curve, `mapActions` and `glyph` |
| `index.ts` | `Input`: listeners, per-frame merge of keyboard, gamepad and touch, edges, sprint burst, rumble |
| `touch.ts` | Touch source: `TouchPad` (pointer bookkeeping, no DOM) and `TouchOverlay` (thumb buttons and the floating stick) |

## Sources

`input.source` is `'keyboard'`, `'gamepad'` or `'touch'`: the device the player used last. Prompts
(`input.prompt(action)`) show gamepad glyphs for gamepad and touch, key names for the keyboard. Per action
the frame takes the largest magnitude of the three sources.

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
  Pause / Resume, and Back (`menuBack`) while a shop menu is open. A button holds its action until lifted; a tap
  between two frames is still a press edge. `main.ts` calls `input.setTouchContext('foot' | 'car' | 'menu' |
  'paused' | 'hidden')` every frame to pick the visible set; the overlay is hidden unless the source is touch.
- **Multi-touch**: Pointer Events tracked by `pointerId`, so stick, look and buttons work at once. The canvas has
  `touch-action: none`; each touch is captured by the element it started on, so a finger that started on a
  button never selects a shop row under it.

Styles (`#touch-controls`, `.touch-*`) live in `game/index.html` and respect the safe-area insets.

## Tests

```sh
cd game && npx vitest run tests/unit/input.test.ts tests/unit/touch.test.ts
# Chrome, synthetic tiles, both backends (after make game-build):
cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test gamepad.spec.ts touch.spec.ts
```

`tests/e2e/touch.spec.ts` runs at 844×390 with `hasTouch` and `isMobile`, sends multi-finger touches through
CDP (`Input.dispatchTouchEvent`) and writes screenshots to `$TURKU3D_WORK/reports/gameplay-touch-controls/`.
