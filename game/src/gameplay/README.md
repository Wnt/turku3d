# Gameplay

`contract.ts` is the shared interface between the mission system and the minimap/router: targets,
`MissionHudState`, `MissionHudSource` and `Router`. It changes only with a message to both owners.

## Missions

Story, locations, economy and the state machine are specified in
[docs/design/story.md](../../../docs/design/story.md).

| File | Role |
|---|---|
| `missions/missions.ts` | `MissionSystem`: deterministic state machine (`idle → licence_needed → at_pizzeria → carrying → delivered \| failed`), delivery timer, money, licence, vehicle ownership, dialogue queue; implements `MissionHudSource`; persists to `localStorage` key `turku3d.missions` |
| `missions/straightRouter.ts` | Straight-line `Router` that `main.ts` injects until the street-network router is wired |
| `economy.ts` | Prices, base pay, tip formula, par time, deadline |
| `content/locations.ts` | Pizzeria, black market and dealer targets, and the bought-vehicle kerb spot |
| `content/deliveries.ts` | Delivery missions in play order (repeat after the last) |
| `content/dialogue.json` | Every displayed line: intro, hints, prompts, notices, pay lines, character dialogue |
| `content/dialogue.ts` | Typed lookup by line id (`lineText`, `dialogueLine`) |

`main.ts` constructs the system with the router and `localStorage`, calls
`missions.update(dt, { position, interact, input, vehicleId })` every frame (dt 0 while paused or on
the start screen), asks `missions.mayDrive(id)` before entering a vehicle, caps the player's car at
bicycle speed when the bicycle is owned, and moves the player's car to the dealer kerb after a
purchase. The interaction button is the `mission` input action (Xbox A / keyboard E); targets act
within 3 m. The start screen shows the intro lines unless `hasSavedGame(localStorage)`, and
`missions.begin()` runs when it closes.
`window.__game.missions` and `__game.state().missions` expose the system and a snapshot for tests.

### Adding a delivery

1. Pick an address inside the published area and a street-side point in front of it. Check that the
   point is walkable and has no building above it in the published collision tiles.
2. Add the lines `hint_deliver_<n>`, `pizzeria_handover_<n>`, `customer_<n>_fast`,
   `customer_<n>_ok` and `customer_<n>_slow` to `content/dialogue.json` (hints are
   `"voiced": false`).
3. Append `delivery(<n>, '<address>', { x, z })` to `DELIVERIES` in `content/deliveries.ts`, keeping
   the list ordered by route distance, and add the row to the table in `docs/design/story.md`.
4. Run `npx vitest run tests/unit/missions.test.ts` from `game/`: it checks that every referenced
   line exists and that prices quoted in lines match `economy.ts`.

A saved game stores only the delivery index, so adding deliveries keeps existing saves valid.

### Tests

```sh
cd game
npx vitest run tests/unit/missions.test.ts tests/unit/input.test.ts
# Chrome check on the real tiles (build the game first: make game-build)
python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts tests/e2e-tile/missions.spec.ts
```

The Chrome check writes screenshots of each prompt to `$TURKU3D_WORK/reports/gameplay-missions/`.
