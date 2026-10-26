# Story and first missions: the courier

Status: Phase 4 first slice (gameplay-missions, 2026-10-26). All in-game text is English
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
platform requires a work licence first. The only licences left are sold on the black market by people
who joined the platform economy at the right time. Fast deliveries earn tips. A bicycle or a car can be
bought from a dealer at the river end of Kristiinankatu, where it meets Läntinen Rantakatu.

The start screen shows the intro (lines `intro_1`, `intro_2`, `intro_3`) the first time, that is,
while no saved game exists:

> Turku, 2027. Last month you were a software developer. Then came the redundancy talks, and
> thousands of jobs like yours went to AI.
>
> The austerity package abolished unemployment benefits, earnings-related and basic alike. The only
> work left is the work robots cannot yet do on their own.
>
> A traditional pizzeria on Linnankatu needs a courier, but first you need a work licence, and the
> only ones left are sold on the black market. You have 120 euros.

## Locations

All businesses are fictional. Coordinates are engine metres (`x = E − 23459500`,
`z = −(N − 6704500)`), inside the published four-tile area (x, z in 0…500). Every point was checked
against the published collision tiles (`$TURKU3D_DATA/live/tiles/collision`): it lies on walkable
ground with no building above it.

| Target id | Kind | Fiction | Real place | x, z | EPSG:3877 E, N | Ground |
|---|---|---|---|---|---|---|
| `pizzeria` | `pickup` | Pizzeria Linnanportti, an old family pizzeria | Linnankatu 17 street front (the OSM extract has a pizza restaurant at this address, inside the area) | 77.1, 187.6 | 23459577.1, 6704312.4 | sidewalk, H 3.96 |
| `market` | `market` | Licence seller, no name | Lower quay under the west end of Auransilta; reached by the stairs at x 296, z 198 or the ramp under the deck | 297.0, 205.5 | 23459797.0, 6704294.5 | paving at H 1.1, deck above at H 5.7 |
| `dealer` | `shop` | Rantakadun Kulkine, a used-vehicle dealer | Corner of Kristiinankatu and Läntinen Rantakatu at the riverbank | 101.5, 290.5 | 23459601.5, 6704209.5 | sidewalk, H 3.45 |

A bought vehicle waits at the kerb next to the dealer, x 119, z 286.5, facing along Läntinen
Rantakatu (`DEALER_VEHICLE_SPOT`).

## Economy

Defaults live in [`game/src/gameplay/economy.ts`](../../game/src/gameplay/economy.ts). Money is whole
euros. A unit test checks that dialogue lines quoting a price or pay match these constants.

| Item | Value |
|---|---|
| Start money | 120 € |
| Work licence | 80 € |
| Bicycle | 60 € |
| Car | 350 € |
| Base pay per delivery | 6 € |
| Tip | 0–8 €: 8 € up to 70 % of the par time, falling linearly to 0 € at 130 %, rounded to whole euros |
| Par time | route distance ÷ speed + 20 s; speed 1.4 m/s on foot, 5 m/s bicycle, 9 m/s car |
| Delivery deadline | 2 × par time; after that the customer cancels (no pay) |

The route distance is the `Router` path length from the pizzeria to the drop-off. If the router has
no path (either end off the graph), the straight-line distance is used.

The par speed is that of the vehicle the player **owns**. While carrying a pizza the player may travel
only on foot or with their own vehicle: entering any other car is refused with the notice
`notice_own_vehicle_only`, and so is a pickup from a borrowed car. Free roam outside a delivery is
unchanged, so every car stays drivable then. Faster vehicles do not raise the tip (the par is fair
for each vehicle); they raise the earnings rate, because more deliveries fit in the same time.

The dealer sells nothing before the work licence is bought (the start money would otherwise fall
short of the licence and no income would be possible). After that the dealer offers the bicycle,
or the car once it is affordable or the bicycle is owned.

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
`update(dt, frame)` (with the player position, the interaction button, the input device and the
driven vehicle) and in `begin()` and `mayDrive()`.

```
idle ──begin()──▶ licence_needed ──buy licence at market──▶ at_pizzeria ──take pizza──▶ carrying
                                                                ▲                         │
                                                                │      hand over ◀────────┤
                                                                │   (pay + tip)           │ deadline passed
                                                                └── delivered ◀───────────┤
                                                                └── failed ◀──────────────┘
```

- `idle`: a new game before the start screen is dismissed. `begin()` moves to `licence_needed`.
- `licence_needed`: active target `market`. Within 3 m the button buys the licence (80 €).
- `at_pizzeria`: active target `pizzeria`. Within 3 m the button takes the pizza: the par time is
  computed and the delivery timer starts.
- `carrying`: active target is the drop-off. The timer (`timerSeconds`) counts down to the
  deadline. Within 3 m the button hands the pizza over: base pay plus tip.
- `delivered` / `failed`: the result lines play, then the machine returns to `at_pizzeria` with the
  next delivery. A failed delivery also moves on to the next address.

The interaction is the A button (Xbox) or E within 3 m (horizontal distance) of a target. The
message line (`MissionHudState.message`) always tells what happens next, in this priority:

1. The dialogue line being spoken (greetings, sales, customer reactions, pay), each shown for
   2.5–7 s depending on its length.
2. The prompt of the target in range, for the current input device (`prompt_*_pad` /
   `prompt_*_key`, "Press A …" / "Press E …"), or a notice when the action is not possible
   (`notice_*`).
3. The step hint (`hint_licence`, `hint_pizzeria`, `hint_deliver_<n>`).

Characters greet once when the player comes within 10 m (again after leaving 16 m): the licence
seller, the pizzeria owner (first day, back after a delivery, after a cancellation, or without a
licence) and the dealer. Customers react at the door by tip band: fast (tip ≥ 5 €), ok (1–4 €),
slow (0 €).

Minimap targets: `pizzeria` and `dealer` always, `market` until the licence is bought, and the
current drop-off while carrying.

State persists in `localStorage` under one JSON key, `turku3d.missions` (phase, money, licence,
vehicle, delivery index, elapsed and par seconds), so a reload continues, including a running
delivery timer. A corrupt or foreign save starts a new game.

## Dialogue

Speakers: `narrator`, `pizzeria` (owner), `market` (licence seller), `dealer`, `customer`. Prompts,
hints, notices and pay lines are narrator lines with `"voiced": false`; character lines and the
intro are voiced. Code references lines only by id; the displayed text equals the line text exactly
so the voice player can match the clip. Rules: `~/turku3d/briefs/dialogue-contract.md`.

## Out of scope for this slice

- Save-game UI, NPC characters or models at the locations, new vehicle or bicycle models.
- Bicycle handling (the car with a speed cap stands in).
- Police, wanted level, theft consequences, or penalties for driving others' cars outside deliveries.
- More pizzerias, random orders, order choice, multiple concurrent orders or a day/night schedule.
- Spending money on anything but the licence and vehicles.
