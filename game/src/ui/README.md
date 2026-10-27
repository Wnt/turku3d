# UI

DOM overlays drawn over the WebGPU/WebGL canvas. Styles live in `game/index.html`.

| File | Role |
|---|---|
| `hud.ts` | Debug HUD (H): backend, frame times, resident tiles, collision, player position |
| `startScreen.ts` | Controls, controller status and the story intro of a new game; closes with A or the button |
| `shopMenu.ts` | `ShopMenu`: the full-screen shop menu that renders a `ShopView` from the mission system |

## Shop menu

`main.ts` calls `shopMenu.render(missions.shop, input)` every frame (cheap when the view object is
unchanged) and, while `shopMenu.open`, `shopMenu.handle(input)` before the mission update. `handle`
moves the selection with `menuUp` / `menuDown` (D-pad, left stick, arrows, W/S) and returns
`{ choose, leave }`: `menuSelect` (A, Enter, E, or a mouse click on a row) chooses the selected
option, `menuBack` (B, Escape, Backspace) leaves. The mission system decides what a choice does;
the menu only shows its result. The layout is the shop's background illustration with a dark
gradient on the left, the shop name, cash and permit counter, the shopkeeper's latest line (the
same text the HUD shows, so the voice clip plays), and one row per option: icon, label, detail
(or the pizzeria's order quote: address · route metres · par m:ss), price (`80 €`, income as
`+400 €`). Greyed rows show the reason instead of the detail. All texts except the button hints and
the status row are dialogue lines (`game/src/gameplay/content/dialogue.json`).

Unit tests (no DOM): `cd game && npx vitest run tests/unit/shops-ui.test.ts`. The DOM, the input
lock and the art are checked in Chrome by `tests/e2e-tile/shops.spec.ts`
(see `game/src/gameplay/README.md`, "Tests").

## Menu art

`game/public/assets/ui/`: three 1280×720 backgrounds and nine 256×256 icons, WebP, each under
200 KB (largest `bg_dealer.webp`, 129 KB; 345 KB in total). They were generated with the project's
Codex image runner and are project-owned (`game/public/assets/LICENSES.md`).

| Job id = file | Used by | Subject (full prompt: `textures/turkutex/ui_art.py`) |
|---|---|---|
| `bg_market` | Black market | Stall under the stone arch of Auransilta at night, bare bulb, wet cobbles, hooded seller from behind |
| `bg_pizzeria` | Pizzeria Linnanportti | Old family pizzeria counter, wood-fired oven, pizza boxes, delivery bag, blank menu board |
| `bg_dealer` | Rantakadun Kulkine | Used-vehicle lot on the Aura riverbank at dusk, string lights, sales cabin |
| `icon_permit` | Rent a courier working permit | Laminated permit card on a lanyard |
| `icon_plates` | Forged license plates | Two blank plates with a rubber band |
| `icon_kidney` | Sell your kidney | Cartoon kidney on ice in a picnic cooler |
| `icon_warning` | Buy back the permit expiry warning | Phone with an alert bubble |
| `icon_pizza_box` | Take the next delivery order | Steaming pizza box |
| `icon_slice` | A slice for yourself | Pizza slice |
| `icon_coins` | Tip rules, sell your vehicle back | Coins and banknotes |
| `icon_bicycle` | Bicycle | City bicycle with a basket |
| `icon_car` | Car | Dented second-hand hatchback |

Every prompt shares one style block (flat-shaded vector illustration, palette navy #141a2e, slate
#2c3550, amber #f2a541, brick #c8553d, teal #3fa7a0, off-white #efe6d8; no text, letters, logos or
watermarks) and a framing line: backgrounds are wide with the subject on the right half because
the menu panel covers the left; icons are one centred object on flat #1b2133, the menu's row colour.
Prompt version `ui-art-v1`, routing kind `library` (`textures/codex-routing.toml`: `gpt-6-astra`,
medium effort).

Generation record (2026-10-27): 12 jobs, 12 image calls, no retries, one PNG each (backgrounds
1672×941, icons 1254×1254), weekly Codex usage 27 % before and 27 % after. Thread ids are in the
run's `out.jsonl`; the content-hash cache makes a rerun of unchanged prompts free.

Regenerate (sandbox environment loaded, `make textures-venv` once):

```sh
cd textures
.venv/bin/python -m turkutex.ui_art jobs "$TURKU3D_WORK/ui-art/jobs.jsonl"
.venv/bin/python -m turkutex codex run "$TURKU3D_WORK/ui-art/jobs.jsonl" --out "$TURKU3D_WORK/ui-art/run1" -j 4
.venv/bin/python -m turkutex.ui_art encode "$TURKU3D_WORK/ui-art/run1" ../game/public/assets/ui
```

`encode` centre-crops each primary PNG to its aspect ratio, scales it with Lanczos and writes WebP at
the highest quality (86 down to 38) that fits 200 KB. To change one picture, edit its subject in
`ART` (or bump `PROMPT_VERSION` for all), rerun the three commands, and look at the result before
committing. Tests: `cd textures && .venv/bin/python -m pytest -q tests/test_ui_art.py`.
