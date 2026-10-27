# Story and first missions: the courier

Status: Phase 4 first slice (gameplay-missions, 2026-10-26); shops, activation zones and the permit
economy (gameplay-shops-ui, 2026-10-27). All in-game text is English
(user decision 2026-10-26). Every displayed line lives in
[`game/src/gameplay/content/dialogue.json`](../../game/src/gameplay/content/dialogue.json), the
single source for the HUD, the start screen and the voice pipeline. This document quotes line ids;
the JSON holds the exact text.

## Premise

Turku, 2027. The protagonist has just lost a job as a software developer in redundancy
negotiations: thousands in the field were replaced by AI. The government's austerity package
abolished both earnings-related and basic unemployment benefits nationwide. The only work left is
work robots cannot yet do fully on their own.

The player starts with a little money. A traditional pizzeria on Linnankatu needs a courier, but the
platform requires a courier permit first. The only permits left are rented out, ten deliveries at a
time, on the black market by people who joined the platform economy at the right time. Renewing the
permit is where the money goes. Fast deliveries earn tips. The same market buys organs and sells
forged plates. A bicycle or a car can be bought from a dealer at the river end of Kristiinankatu,
where it meets Läntinen Rantakatu.

The start screen shows the intro (lines `intro_1`, `intro_2`, `intro_3`) the first time, that is,
while no saved game exists:

> Turku, 2027. Last month you were a software developer. Then came the redundancy talks, and
> thousands of jobs like yours went to AI.
>
> The austerity package abolished unemployment benefits, earnings-related and basic alike. The only
> work left is the work robots cannot yet do on their own.
>
> A traditional pizzeria on Linnankatu needs a courier, but first you need a courier permit, and the
> only ones left are rented out on the black market. You have 120 euros.

## Locations

All businesses are fictional. Coordinates are engine metres (`x = E − 23459500`,
`z = −(N − 6704500)`), inside the published four-tile area (x, z in 0…500). Every point was checked
against the published collision tiles (`$TURKU3D_DATA/live/tiles/collision`): it lies on walkable
ground with no building above it.

| Target id | Kind | Fiction | Real place | x, z | EPSG:3877 E, N | Ground |
|---|---|---|---|---|---|---|
| `pizzeria` | `pickup` | Pizzeria Linnanportti, an old family pizzeria | Linnankatu 17 street front (the OSM extract has a pizza restaurant at this address, inside the area) | 77.1, 187.6 | 23459577.1, 6704312.4 | sidewalk, H 3.96 |
| `market` | `market` | Black market seller, no name | Lower quay under the west end of Auransilta; reached by the stairs at x 296, z 198 or the ramp under the deck | 297.0, 205.5 | 23459797.0, 6704294.5 | paving at H 1.1, deck above at H 5.7 |
| `dealer` | `shop` | Rantakadun Kulkine, a used-vehicle dealer | Corner of Kristiinankatu and Läntinen Rantakatu at the riverbank | 101.5, 290.5 | 23459601.5, 6704209.5 | sidewalk, H 3.45 |

A bought vehicle waits at the kerb next to the dealer, x 119, z 286.5, facing along Läntinen
Rantakatu (`DEALER_VEHICLE_SPOT`).

## Economy

Defaults live in [`game/src/gameplay/economy.ts`](../../game/src/gameplay/economy.ts). Money is whole
euros. A unit test checks that dialogue lines quoting a price or pay match these constants.

| Item | Value |
|---|---|
| Start money | 120 € |
| Courier permit rental | 80 € for the next 10 delivery orders; renting again adds 10, at most 20 ahead |
| Forged license plates | 120 €, once: deliveries count with any car on the street |
| Sell your kidney | +400 €, once; run and sprint speed drop 15 % for good |
| Permit expiry warning (bought back) | 10 €, once: after a hand-over the HUD warns at 2, 1 and 0 deliveries left |
| Slice for yourself | 4 €: full run speed for 120 s (game time) after the kidney sale; no effect otherwise |
| Bicycle | 60 € |
| Car | 350 € |
| Vehicle sold back | half the price, rounded down: 30 € / 175 € |
| Base pay per delivery | 6 € |
| Tip | 0–8 €: 8 € up to 70 % of the par time, falling linearly to 0 € at 130 %, rounded to whole euros |
| Par time | route distance ÷ speed + 20 s; speed 1.4 m/s on foot, 5 m/s bicycle, 9 m/s car |
| Delivery deadline | 2 × par time; after that the customer cancels (no pay) |

The route distance is the `Router` path length from the pizzeria to the drop-off. If the router has
no path (either end off the graph), the straight-line distance is used.

The par speed is that of the vehicle the player **owns**, or a car's when the order is taken from a
borrowed car with forged plates. While carrying a pizza the player may travel only on foot or with
their own vehicle: entering any other car is refused with the notice `notice_own_vehicle_only`, and
the pizzeria greys out the order in a borrowed car (`reason_own_vehicle_only`). Forged plates lift
both limits. Free roam outside a delivery is unchanged, so every car stays drivable then. Faster vehicles do not raise the tip (the par is fair
for each vehicle); they raise the earnings rate, because more deliveries fit in the same time.

The dealer sells nothing before the first permit is rented (the start money would otherwise fall
short of the permit and no income would be possible). The player owns at most one vehicle: buying
another needs the current one sold back first.

Why these numbers: one rental (80 €) costs about as much as ten fast deliveries pay in tips, so on foot
the permit eats most of the margin and a bicycle takes some twenty deliveries. The kidney (+400 €) is
the shortcut to a car, and the 15 % run penalty is what it costs for the rest of the game; the slice
is a cheap way to hide that for two minutes.

**Bicycle stand-in.** There is no bicycle model in this slice. A bought bicycle is the player's
existing car (`player` vehicle) with its throttle cut above 6 m/s (`BICYCLE_TOP_SPEED`). Buying
either vehicle moves that car to the dealer's kerb. A bicycle model and handling are follow-ups.

## Deliveries

Drop-off points are street-side spots in front of real addresses from the OSM extract, inside the
area. Distances are shortest walking routes on the OSM street graph from the pizzeria (the game's
street-network router may differ by a few metres). After the fourth delivery the list repeats.

| # | Address | x, z | Route | Par on foot |
|---|---|---|---|---|
| 1 | Linnankatu 13a | 191.0, 106.5 | ~140 m | ~120 s |
| 2 | Aurakatu 1 | 294.5, 185.2 | ~290 m | ~227 s |
| 3 | Läntinen Rantakatu 3 | 439.5, 54.8 | ~440 m | ~336 s |
| 4 | Kaskenkatu 4a (across Auransilta, up the hill) | 474.3, 402.0 | ~610 m | ~456 s |

## Mission state machine

`game/src/gameplay/missions/missions.ts`, `MissionSystem`. It is deterministic: it changes only in
`update(dt, frame)` (with the player position, the interaction button, the input device, the
driven vehicle and the menu's `choose` / `leave` command) and in `begin()` and `mayDrive()`.

```
idle ──begin()──▶ permit_needed ──rent permit (menu)──▶ at_pizzeria ──take order (menu)──▶ carrying
                                                                ▲                         │
                                                                │      hand over ◀────────┤
                                                                │   (pay + tip)           │ deadline passed
                                                                └── delivered ◀───────────┤
                                                                └── failed ◀──────────────┘
```

- `idle`: a new game before the start screen is dismissed. `begin()` moves to `permit_needed`, or
  to `at_pizzeria` when a permit is left.
- `permit_needed`: no permit deliveries left; active target `market`. Renting a permit in its menu
  moves to `at_pizzeria`.
- `at_pizzeria`: active target `pizzeria`. Taking the order in its menu uses one permit delivery,
  computes the par time and starts the delivery timer.
- `carrying`: active target is the drop-off. The timer (`timerSeconds`) counts down to the
  deadline. Within 3 m the button hands the pizza over: base pay plus tip.
- `delivered` / `failed`: the result lines play (plus the permit warning, when bought back), then
  the machine returns to `at_pizzeria` with the next delivery, or to `permit_needed` when the
  permit is used up. A failed delivery also moves on to the next address.

The interaction is the A button (Xbox) or E within 3 m (horizontal distance) of a target. At a shop
it opens the shop's menu; at the drop-off it hands the pizza over. The message line
(`MissionHudState.message`) always tells what happens next, in this priority:

1. The dialogue line being spoken (greetings, replies, customer reactions, pay), each shown for
   2.5–7 s depending on its length.
2. Nothing while a shop menu is open (the menu shows the shopkeeper's line).
3. The prompt of the target in range, for the current input device (`prompt_enter_<shop>_pad` /
   `_key`, `prompt_hand_over_*`: "Press A …" / "Press E …").
4. The step hint (`hint_permit`, `hint_pizzeria`, `hint_deliver_<n>`).

Customers react at the door by tip band: fast (tip ≥ 5 €), ok (1–4 €), slow (0 €).

Minimap targets and world zones: `pizzeria`, `dealer` and `market` always, and the current
drop-off while carrying (its zone disappears with the hand-over until the next order).

State persists in `localStorage` under one JSON key, `turku3d.missions`, format version 2: phase,
money, permit deliveries left, plates, kidney, warning, vehicle, delivery index, elapsed and par
seconds. A reload continues, including a running delivery timer; slice energy is not saved. A
version 1 save (main 2673ec0: `licence` instead of the permit) is migrated on load: an owned licence
becomes one fresh rental of 10 deliveries and `licence_needed` becomes `permit_needed`. A corrupt or
foreign save starts a new game.

## Shops

Every target has a visible activation zone in the world (`gameplay/zones.ts`): a ring of radius
3.5 m (the 3 m interaction radius plus a margin) with a short light wall, in the minimap colour of
its `TargetKind`, pulsing gently and fading out between 50 and 75 m, with a floating label up to
60 m (`zone_market` "Black market", `zone_pizzeria`, `zone_dealer`, `zone_dropoff` "Deliver here").

Entering a shop (A / E in its zone) opens a full-screen menu (`ui/shopMenu.ts`) over the shop's
illustration. World movement and the camera are locked until it closes: B / Escape or the Leave
option, or a purchase that ends the visit (taking an order, buying a vehicle). D-pad or left stick
up/down (arrows or W/S) move the selection, A (Enter or E) selects. The shopkeeper greets when the
menu opens and answers each choice; those lines are voiced and are also the HUD message. Options
the player cannot afford or that are not available are greyed with the reason (`reason_*`);
selecting one the player cannot afford makes the shopkeeper refuse (`*_no_money`).

| Shop | Greeting | Options (menu order) |
|---|---|---|
| Black market | `market_greet`; `market_greet_expired` once the permit has run out | Rent a courier working permit (80 €), Forged license plates (120 €), Sell your kidney (+400 €), Buy back the permit expiry warning (10 €), Leave |
| Pizzeria Linnanportti | `pizzeria_first`, `pizzeria_back`, `pizzeria_after_fail`, `pizzeria_no_permit`, `pizzeria_carrying` or `pizzeria_greet` | Take the next delivery order (shows address, route metres and par time; ends the visit), A slice for yourself (4 €), Ask about the tip rules (free, `pizzeria_tip_rules`), Leave |
| Rantakadun Kulkine (dealer) | `dealer_greet`; `dealer_no_permit` before the first permit, with every option greyed | Bicycle (60 €), Car (350 €) (both end the visit; the vehicle waits at the kerb), Sell your vehicle back (+half), Leave |

The drop-off has no menu: the button hands over the pizza, the customer's line and the pay line
play, and the zone disappears until the next order.

## Dialogue

Speakers: `narrator`, `pizzeria` (owner), `market` (black market seller), `dealer`, `customer`. Prompts,
hints, notices, pay lines, zone labels and menu option labels, details and reasons are narrator
lines with `"voiced": false`; character lines (greetings and replies) and the intro are voiced. Code references lines only by id; the displayed text equals the line text exactly
so the voice player can match the clip. Rules: `~/turku3d/briefs/dialogue-contract.md`.

## Out of scope for this slice

- Save-game UI, NPC characters or models at the locations, new vehicle or bicycle models.
- A HUD permit counter outside the menus (the bought-back warning is the in-world reminder).
- Bicycle handling (the car with a speed cap stands in).
- Police, wanted level, theft consequences, or penalties for driving others' cars outside deliveries.
- More pizzerias, random orders, order choice, multiple concurrent orders or a day/night schedule.
