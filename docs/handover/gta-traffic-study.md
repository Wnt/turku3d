# GTA V traffic study: behaviour spec for Turku3D traffic

Clean-room study of how GTA V Enhanced (1.0.1158.16) keeps ambient traffic flowing. This is a
behaviour and parameter spec written in our own words. Nothing from the game ships with it; raw
material stays in `~/turku3d/private/gta-traffic/` (Mac) and `C:\Research\gta-traffic\` (host).

Evidence tags used throughout:

- **[F]** read from the game's own data files (RPF archives, read with a CodeWalker-library probe)
- **[S]** game executable strings, or decompiled game scripts (public Enhanced 1.73 dump) with call counts
- **[N]** community native documentation (alloc8or/gta5-nativedb-data, alloc8or.re enum docs)
- **[P]** public modding knowledge (CodeWalker source/editor, FiveM/driving-style docs), not checked against code
- **[O]** observed in the running game (story mode, 17:30, clear weather, CityDensity 1.0, downtown)
- **[I]** inferred by us from the above

The core answer to "how does it run for hours": **GTA does not dissolve jams.** It never has to,
because every ambient vehicle the player cannot see is disposable. Vehicles out of view more than
about 150 m from the player are removed, the total count is held to a per-zone, per-hour budget, and
new vehicles are created on the edge of a spawn shape around the player. A queue that forms out of
sight is deleted, and the cars come back as fresh traffic somewhere else. In view, GTA tolerates jams
(see 5.3).

---

## 1. Population and density

### 1.1 Budgets: how many cars exist [F]

- Global caps from `gameconfig.xml` (gen9 value / `_Base` low-spec value):
  - `VehicleUpperLimit` is 130 (base 40). In multiplayer `VehicleUpperLimitMP` is 60.
  - `VehicleParkedUpperLimit` is 100 (base 40).
  - Pools: Vehicles 300, Peds 256. `PedsForVehicles` is 75.
- Per-zone schedules come from `popcycle.dat`: 116 schedules, each with 12 two-hour slots. Each row
  gives the max ambient peds, scenario peds, **max cars**, parked cars, low-priority parked cars,
  cop-car %, cop-ped %, and the vehicle model-group mix with % weights.
  - Car caps across all schedules: median 60 at 02-06 h, 85 at 06-12 h, 100 at 12-14 h and
    16-18 h, 85 in the evening. The minimum is 20 and the maximum 100.
  - DOWN_TOWN cars per slot from 00 h: 60 60 60 80 80 100 100 100 100 80 80 80.
    FREEWAY: 60 60 60 100 x7 80.
  - MP variants (`NET_*`) are flat all day. NET_DOWN_TOWN is 85 in every slot except 60 at 02-06 h,
    and NET_FREEWAY is 85 in every slot.
- `zonebind.ymt` binds 1,306 map zones (axis-aligned boxes in `popzone.ipl`) to an SP and an MP
  schedule. It also sets `popRangeScaleStart/End` (non-zero in only 39 zones, e.g. 170→200 m) and
  `popRangeMultiplier` (2 in 43 zones). Some zones get a larger population radius (open areas).
- Global multipliers: `VehicleAmbientDensityMultiplier` 100 %, parked 100 %. In MP,
  `vehiclepopulation.xml` sets the random-vehicle density to 1.0 and parked to 0.5.
- The user's "Population Density" setting scales all of this. Observed [O]: within 450 m there were
  37 ambient vehicles at CityDensity 0 versus 103-122 at CityDensity 1.

### 1.2 Density per road: where the cars go [F]

- Every path node carries a **density 0-15** (4 bits). Density converts to a **target spacing between
  vehicles** (`VehicleSpacing`, metres per car):

  | density | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | spacing m | 172 | 150 | 129 | 110 | 88 | 55 | 52 | 45 | 40 | 34 | 27 | 22 | 20 | 19 | 18 |

- Density 0 means no ambient traffic.
- Densities actually used on switched-on nodes: 1 (5,206 nodes), 2 (1,717), 3 (2,532), 4 (2,916),
  5 (1,224), 6 (4,938), 8 (5,509), 10 (2,443) and 12 (2,965). Density 15 is used almost only on
  switched-off nodes.
- Mean density by road speed class is 1.6 / 4.6 / 6.2 / 8.1 for slow / normal / fast / faster.
- **Player-road bias**: the road the player is on gets +2 density for node densities 6-10 and +1 for
  11 (`PlayerRoadDensityInc`). Roads the player is NOT on lose density on high-density nodes
  (`NonPlayerRoadDensityDec`): −1 at 11, −2 at 12, −3 at 13, −4 at 14, −5 at 15.
  - [I] Busy arterials the player cannot see are thinned, and the player's own road looks busy.
- Scan radius for "roads near players" is 330 m (`PlayersRoadScanDistance`).
- [I] Desired car count = Σ over lanes of the switched-on road length within the scan radius,
  divided by spacing(density), clamped by the popcycle car cap and the global upper limit. A debug
  string "Desired Number of Ambient Vehicles : %d" [S] confirms that such a target exists.

### 1.3 Spawn shape and distance bands [F params, P/I interpretation]

The game uses a "keyhole" creation shape around the player (gen9 / base values):

| parameter | gen9 | base |
|---|---|---|
| inner radius | 120 m | 80 m |
| outer radius | 215 m | 185 m |
| inner band thickness | 20 m | 20 m |
| outer band thickness | 40 m | 40 m |
| side wall thickness | 60 m | 60 m |

- [P/I] The shape is a circle of the inner radius plus a forward slot (wedge) out to the outer radius
  in the camera direction. New cars are created only **inside the thin bands on the shape's
  boundary**:
  - around 120-140 m to the sides and behind, where they are not seen;
  - around 215-255 m ahead, where distance hides the pop-in.
- `VehicleMaxCreationDistance` is 255 m on screen. `VehicleMaxCreationDistanceOffscreen` is 50 m
  ([I] an extra allowance for creation points that are hidden; exact use unknown).
- **Removal (cull) ranges**:
  - `VehicleCullRange` 311 m, multiplied by `VehicleCullRangeOnScreenScale` 140 % = about 435 m for
    vehicles on screen.
  - `VehicleCullRangeOffScreen` **150 m** (base 100 m) for vehicles off screen.
- Creation must also respect `identicalModelSpawnDistance` (20 m for 1,651 models, 40-80 m for
  others) and per-model `maxNum` [F vehicles.meta].
- Observed [O] (camera 110 m above a downtown junction, counts by distance from the player and by
  front/back half-plane):
  - Looking north: the front bands at 0-100 / 100-200 / 200-300 / 300-400 / 400+ m held
    7 / 10 / 20 / 45 / 15 cars. The back held 9 / 6 / **0 / 0 / 0**.
  - After a 180° camera turn, the newly hidden far bands (62 cars) were empty at the **next sample
    (≤10 s)**. The newly visible far bands reached steady state (about 30 / 35 cars) in **20-30 s**.
    The total stayed 93-122, so the budget held throughout.
  - Out of view, cars exist only within about 200 m of the player.
- Population runs at 25 Hz in SP (20 Hz MP), one cycle per frame [F]. Ambient AI is timesliced: at
  most 10 vehicle updates per frame (base 4) [F].

### 1.4 Removal and recycling [F/S, I]

- `DensityBasedRemovalRateScale` is 36 and `DensityBasedRemovalTargetHeadroom` is 10 [F].
  - [I] When the live count exceeds (target − headroom), vehicles are removed at a rate
    proportional to the excess, preferring hidden, distant ones.
  - The headroom keeps about 10 free slots so new creations at the spawn edge never starve.
- The executable prints per-frame counters "ambient / mission / parked / **reusePool** / deleted /
  net" and "real (traffic) / **dummy** / **superdummy**" [S].
  - Removed ambient vehicles go to a **reuse pool**. They are re-spawned instead of being destroyed
    and re-created [P/I].
  - Vehicles have three simulation LODs: real physics near the player; dummy (simplified, wheels
    constrained to the road surface); superdummy (kinematic along the path, no collision) [S
    names, P behaviour].
- Population type matters: only RANDOM_AMBIENT and RANDOM_PARKED vehicles are culled. Mission and
  permanent vehicles are not [N ePopulationType].

### 1.5 Script-level controls (how missions bend population) [S, N]

Calls counted in the decompiled scripts:

| native | calls | typical values and use |
|---|---|---|
| SET_VEHICLE_DENSITY_MULTIPLIER_THIS_FRAME | 553 | 0.0 (362×), 1.0 (68×), 0.5, 0.25, 0.2, 0.1; set every frame while a mission area must be calm |
| SET_RANDOM_VEHICLE_DENSITY_MULTIPLIER_THIS_FRAME | 166 | moving traffic only; mostly 0 |
| SET_PARKED_VEHICLE_DENSITY_MULTIPLIER_THIS_FRAME | 139 | parked cars only |
| SET_VEHICLE_POPULATION_BUDGET | 361 | 0-3 (3 = normal, 0 = none); lowers the budget during heavy scenes |
| CLEAR_AREA_OF_VEHICLES | 2,061 | hard reset of an area before and after scenes |
| SET_ROADS_IN_AREA / SET_ROADS_IN_ANGLED_AREA | 257 / 343 | switch nodes off and on; SET_ROADS_BACK_TO_ORIGINAL restores them |
| SET_POP_CONTROL_SPHERE_THIS_FRAME | 46× (…, 100, 200) | moves the population origin to a point, min 100 m, max 200 m |
| SET_AMBIENT_VEHICLE_RANGE_MULTIPLIER_THIS_FRAME | — | 1.5-2.0 for aerial or zoomed views |
| INSTANTLY_FILL_VEHICLE_POPULATION | — | after teleports or cutscenes, fills to target at once |

SET_ROADS_IN_ANGLED_AREA is networked as road-node world state in MP [N].

---

## 2. Path network (YND) [F, P for field names]

Totals (update version, 780 node files):

- 79,545 nodes, 69,008 of them vehicle nodes.
- 148,270 directed link records. Each road segment is stored from both ends, with lane counts "to"
  and "from".

Node fields:

- **Position** is quantised to 0.25 m in x/y and 1/32 m in z.
- **Speed class** has 2 bits: slow 4,223, normal 60,269, fast 1,446, faster 3,080.
- **Density** 0-15 (section 1.2).
- **Special type**:
  - 15 traffic-light stop node (1,237)
  - 16 stop/give-way node (291)
  - 17 caution/slow-down (120, before barriers and merges)
  - 19 emergency-only (82)
  - 20 off-road junction (2,359)
  - 2 parking (4)
- **Flags**:
  - `Junction` (4,083)
  - `HighwayOrLowBridge` (6,447)
  - `NoBigVehicles` (11,451)
  - `Offroad` (13,167)
  - `NoGps` (13,472)
  - `SlipLane` (944)
  - `Tunnel` (405)
  - `CannotGoLeft` / `CannotGoRight` (389 / 161)
  - `LeftTurnOnly` (384)
  - `IndicateKeepLeft` / `IndicateKeepRight` (32 / 62)
  - `HasJunctionHeightmap` (3,110)
  - dead-endness 0-7 (non-zero on 11,467 nodes)
  - `SwitchedOff` plus a `SwitchedOffOriginal` copy for restore
- A heuristic field holds the distance to some reference, for A*.
- **Switched-off nodes are 56.8 % of all vehicle nodes (39,214), and only 29,804 are switched on.**
  - Switched-off nodes are mostly the small network: 95 % have single-lane links; 34 % are no-GPS,
    32 % off-road, 27 % dead-end and 39 % narrow.
  - Ambient traffic spawns and wanders only on switched-on nodes. A driving-style bit (section 4.1)
    lets scripted drivers use switched-off nodes.
  - [I] This one flag keeps ambient cars out of alleys, car parks, cul-de-sacs and service roads,
    which is where gridlock usually starts.

Link fields:

- **Lanes**: lanes-to and lanes-from, 3 bits each. Distribution:

  | lanes (to, from) | share |
  |---|---|
  | 1, 1 | 68.7 % |
  | 1, 0 and 0, 1 | 8.6 % each |
  | 2, 0 and 0, 2 | 5.6 % each |
  | 2, 2 | 1.2 % |
  | 4, 0 / 0, 4 (motorway) | 0.3 % each |

  - Multi-lane roads are mostly modelled as two separate one-way carriageways (2+0 / 0+2).
  - In downtown, the typical node is a 4-link node joining a two-way side street (1,1) and a one-way
    2-lane carriageway.
- **Width / centre offset** has 4 bits. It is 0 on 56 % of links and 15 on 42 %; 1-7 appears on
  about 2 % (median gaps on split roads). [I] Lanes are offset from the link centre line by this
  value; 15 looks like a sentinel.
- Other link flags:
  - `NarrowRoad` 25 %
  - `Shortcut` 3.5 % (used only with the short-cut driving bit)
  - `DontUseForNavigation`
  - `LeadsToDeadEnd` / `LeadsFromDeadEnd`
  - `BlockIfNoLanes`
  - tilt and tilt falloff (banking)
- Link length: p10 4 m, p50 8 m, p90 17 m. Nodes are dense: one about every 8-12 m, so one link is
  roughly one car length.
- Node degree: 2 (60,676), 3 (5,249), 4 (1,801), and 5-12 (556).
- **Junctions**:
  - Junction nodes have 3 links (3,208), 4 (561) or 5-12 (314).
  - 3,356 junctions are a single node. 226 are 2-node clusters, 65 have 3 nodes and 19 have 4-7
    (wide intersections).
  - Junctions with heightmaps store a small z grid (6-13 × 6-13 cells) so cars follow a crowned
    intersection surface.
- **Stop nodes before junctions** (graph distance to the nearest junction node, p10 / p50 / p90):
  - traffic-light stop: 11.9 / 17.2 / 23.6 m
  - stop sign: 6.2 / 12.2 / 30.4 m
  - caution: 14.9 / 28.1 / 42.8 m
  - [I] Cars hold at the stop node, not at the junction centre, which leaves the junction box clear.

---

## 3. Junction negotiation and signals

### 3.1 Signal templates [F junctions.xml]

132 hand-tuned junction templates cover the big signalised intersections. Each has:

- the junction node(s) and a bounding box;
- **entrances**: the node position on each approach, phase index, stopping-distance offset,
  orientation, angle from centre, `CanTurnRightOnRedLight`, `LeftLaneIsAheadOnly`,
  `RightLaneIsRightOnly`, and `LeftFilterLanePhase` (a separate protected-left phase, or −1);
- **phases** with a start time and duration;
- traffic-light prop locations.

Statistics:

- Entrances: 4 (33 templates), 5 (26), 7 (18), 6 (17), 8 (15), 3 (15). 685 in total.
- Phases: 5 (66), 4 (37), 3 (13). Phase duration is **10 s** in 548 of 590 phases (20 s 18×,
  15 s 12×).
- Cycle length p10 / p50 / p90 is 30 / 50 / 60 s.
- Right-on-red is allowed at 273 of 685 entrances (40 %). 332 entrances (48 %) have a separate
  left-filter phase. 179 have a left lane that goes ahead only, and 97 a right lane that turns
  right only.
- Stopping-distance offset: mostly 0; p90 3.4 m; 126 are −1 (default).
- `AutoJunctionAdjustments`: every other signalised junction is an **auto junction**, with phases
  derived at runtime from its approaches. Two of them carry overrides with a cycle of 30 s and an
  offset of 0 or 25 s.
- [I] Auto junctions run one phase per approach group at about 10 s. An offset lets adjacent
  lights form simple green waves.

### 3.2 Right of way without lights [F flags, P/I rules]

- Stop/give-way nodes (special 16) make the car come to a halt at the node. It then enters only when
  the junction is clear of conflicting cars.
- Merges and slip lanes (SlipLane flag, caution nodes) slow the car and make it yield to the main
  road.
- Drivers query `IS_VEHICLE_STOPPED_AT_TRAFFIC_LIGHTS` [N], so "stopped for a light" is a distinct
  AI state from "stopped behind a car".
- [I] Red/amber/green state is per entrance phase. Amber lasts about 2-3 s at the end of each
  phase. A car that cannot stop within its brake distance (section 4.3) proceeds on amber.
- [O] In the 17:30 downtown samples, 8-30 % of ambient cars within 450 m were stationary at any
  moment (queues at lights). The light cycle cleared them without spill-back over 8 minutes of
  observation.

---

## 4. Driving: lane following, lane changes, obstacles

### 4.1 Driving-style bit field [N, P]

Ambient and scripted drivers carry a 32-bit driving style. Bits:

| bit | name | bit | name |
|---|---|---|---|
| 0 | stop for vehicles | 10 | reverse |
| 1 | stop for peds | 11 | wander fallback instead of a straight line |
| 2 | swerve around all vehicles | 12 | avoid restricted areas |
| 3 | steer around stationary vehicles | 13 | no background pathfinding |
| 4 | steer around peds | 14 | adjust cruise speed to road speed |
| 5 | steer around objects | 18 | use shortcut links |
| 6 | don't steer around the player | 19 | change lanes around obstructions |
| 7 | stop at traffic lights | 21 | use switched-off nodes |
| 8 | go off-road when avoiding | 24 | force a straight line |
| 9 | allow wrong-way / oncoming | 29 | avoid highways |
|  |  | 30 | force joining in road direction |

Wander styles used in the scripts, with the bits they set:

| style | uses | bits | meaning |
|---|---|---|---|
| 786603 | 38 | 0, 1, 3, 5, 7, 18, 19 | "normal": stops for cars, peds and lights; steers around stationary cars and objects; changes lanes around obstructions |
| 786599 | 46 | 0, 1, 2, 5, 7, 18, 19 | as normal, but swerves around all cars |
| 786597 | 23 | 0, 2, 5, 7, 18, 19 | as above, minus stopping for peds |
| 786469 | 36 | 0, 2, 5, 18, 19 | no light stops (fast or rushed) |
| 786468 | 27 | 2, 5, 18, 19 | reckless: no stopping for cars |

- The tuning knobs on top of the style are driver ability (0-1) and aggressiveness (0-1).
  - Ambient values are rolled per ped from `pedpersonality.ymt` ranges on a 0-10 scale. Most
    personalities span the full 0-10 for both; some have narrow ranges (e.g. ability 3-6) [F].
  - Mission drivers are set to 1.0 / 1.0 (about 130 calls each) [S].
- `SET_DRIVER_RACING_MODIFIER` (0.2 / 0.5 / 1.0) is used for races only.
- [P/I] Aggressiveness shortens the following gap and the waiting time before overtaking. It also
  raises acceleration and how readily the driver runs amber. Ability affects line quality and the
  chance of minor collisions.

### 4.2 Cruise and wander [N, P, I]

- Ambient cars run a cruise/wander task: pick the next link at each junction at random, weighted to
  prefer continuing, and avoid dead-end links and switched-off nodes.
- The cruise task keeps a short route window of a handful of nodes ahead and re-plans as it goes.
- Cruise speed is per driver (scripts usually pass 10-20 m/s). With bit 14 it adapts to the node
  speed class.
- Lane choice: stay in the current lane, and move to the lane needed for the next turn before the
  junction. Node flags `CannotGoLeft/Right`, `LeftTurnOnly` and the template lane rules restrict
  turns.

### 4.3 Speed control [F vehicleaihandlinginfo.meta]

Four AI handling classes: AVERAGE (972 vehicles), SPORTS_CAR (110), TRUCK (102), CRAP (19).

| class | brake distance | max speed at brake distance | curve speed table, turn angle (degrees) → speed (game units; [I] m/s) |
|---|---|---|---|
| AVERAGE | 10-80 m | 30 | 0→45, 6→35, 12→25, 22→20, 28→15, 40→12.5, 90→10, 180→4 |
| SPORTS_CAR | 10-80 m | 50 | 0→60, 2→50, 6→35, 12→25, 22→20, 28→18, 40→16, 90→10, 180→4 |
| TRUCK, CRAP | 10-100 m | 30 | 0→45, 3→20, 28→15, 40→11, 90→10, 180→4 |

- The absolute minimum speed is 1 for every class.
- [I] The look-ahead angle is the heading change along the next part of the path. Speed is
  interpolated from the table and capped by cruise speed. The braking start distance is scaled
  between the min and max brake distance by current speed.

### 4.4 Obstacles, lane changes, overtaking [N bits, O, I]

- **Stationary car ahead in the same lane, mid-block, with a free adjacent same-direction lane**
  (bits 3 and 19):
  - slow down;
  - wait a short time (the impatience timer, shorter with aggressiveness);
  - indicate, change lane, pass, and return only if the next turn needs it.
  - [O] On a 2-lane one-way carriageway, with the player's stopped car in the right lane, approaching
    cars used the left lane and no queue formed in 2 min.
- **Stationary car close to a signalised stop line** (within about 20 m):
  - treated as part of a queue; cars wait and do not change lanes around it.
  - [O] A stopped car 20 m before a 3-lane signal approach collected a 3-4 car queue behind it.
    The queue stayed for **4+ minutes** through many green phases, with the adjacent lanes
    empty. Visible queued cars were not removed.
- **Single-lane road, oncoming lane** (bits 3/2, without bit 9):
  - pass the obstacle by moving partly over the centre line;
  - only when the oncoming lane is clear for the pass distance;
  - otherwise wait behind it.
  - [P/I] The link width and centre offset give the space available for a partial-lane pass.
- **Swerve** (bit 2): small lateral avoidance of moving cars and objects within the lane width,
  without changing lane.
- Peds and objects are steered around (bits 4/5) or stopped for (bit 1).
- Separate AI tasks exist for swerve, brake, reverse, three-point turn, head-on collision reaction,
  "wait" and "go forward" [N eTaskType]. These are the building blocks of the un-stick manoeuvres
  in section 5.

---

## 5. Stuck and jam handling

### 5.1 Per-vehicle stuck timers [N, S]

- Each vehicle keeps four stuck timers: 0 on roof, 1 on side, 2 hung up (wheels off the ground or
  beached), 3 jammed (trying to move but not moving).
- Script thresholds seen with `IS_VEHICLE_STUCK_TIMER_UP`:

  | timer | threshold | calls |
  |---|---|---|
  | on roof | 7 s | 95 |
  | on side | 40 s | 112 |
  | hung up | 30 s | 86 |
  | jammed | 30 s | 81 |

  Short 1-10 s checks are also used.
- `ADD_VEHICLE_STUCK_CHECK_WITH_WARP(vehicle, minMoveDistance, checkIntervalMs, warpIfStuck,
  warpIfUpsideDown, warpIfInWater, warpMethod)` [N; P for parameter meaning].
  - Typical values are 1-10 m within 1000 ms, or 2 m within 500 ms.
  - If the vehicle has not moved that far within the interval, it is **warped to the nearest road
    node** in its travel direction.
  - [P/I] The warp happens only while the vehicle is not visible. Up to 16 such checks can run at
    once [N].

### 5.2 Ambient AI un-stick sequence [P, I]

When a driving AI car's progress stalls (jammed timer running, no light or queue reason):

1. after about 3-5 s, reverse briefly (TASK_VEHICLE_REVERSE) and retry with a new steering line;
2. on a narrow road, try a three-point turn and re-route;
3. honk (aggressive drivers sooner);
4. if still stuck and not visible, the population system removes it (section 5.3).

### 5.3 The real un-jam trick: hidden vehicles are disposable [F, O, I]

- The off-screen cull radius (150 m) is far smaller than the on-screen one (about 435 m). A hidden
  queue more than about 150 m from the player is deleted, whatever its state. Density-based removal
  (scale 36, headroom 10) additionally trims excess cars even inside that radius.
- Removed cars go to the reuse pool and re-appear at the spawn-shape edge on roads that need cars
  (spacing targets). This acts as a continuous redistribution: congested areas drain and empty
  roads fill.
- The player-road bias thins busy arterials the player is not on (density −1 to −5).
- Visible vehicles are kept even if stuck ([O], section 4.4). The game prefers a visible jam to a
  visible pop. It relies on the player moving or looking away, which happens constantly.
- There is no explicit deadlock-cycle breaker in the data or the native API. The cull makes one
  unnecessary out of view; in view, the stuck timers drive reverse and three-point manoeuvres.

---

## 6. Multiplayer notes (GTA Online)

- MP budgets are lower: `VehicleUpperLimitMP` 60 vs 130; population at 20 Hz vs 25 Hz; parked
  density 0.5 [F].
- MP popcycle schedules are flattened, with no rush-hour peaks (85 cars all day downtown) [F].
- [P/S] Each machine runs vehicle population around its own player and **owns** the ambient cars
  it creates as network objects. Ownership migrates to another player by proximity when the owner
  leaves (strings "takes ownership / keeps ownership", ownership-conflict resolution by priority
  [S]; `NETWORK_DISABLE_PROXIMITY_MIGRATION` [N]).
- A car is removed only when it is out of range and out of view of every player [I]. Road on/off
  switches are replicated world state [N].

---

## 7. What this means for Turku3D (ranked)

Our sim is a deterministic 10 Hz lane-graph flow, shared by all peers. Sources spawn and sinks exit.
Dense districts jam after 30-60 min at peak because full exits back queues up through junctions. A
60-s waits-for-cycle breaker removes one car per cycle.

1. **Hidden-car recycling.** Define "observed" deterministically: within R_view (about 400 m) of any
   player and inside any player's view cone, or within R_near (about 150 m) of any player.
   - Unobserved cars become recyclable. Remove unobserved cars that are waiting (speed < 0.5 m/s for
     more than T, e.g. 20-30 s) or that sit in links with occupancy above a threshold.
   - Remove at a bounded rate (density-based: rate ∝ excess over target − headroom; use about 10 %
     headroom).
   - Re-insert the same number of cars at unobserved source links with spare capacity.
   - All peers derive the same set from shared player poses quantised per tick.
   - This removes the 30-60 min spill-back at its root and makes the cycle breaker rare.
2. **Budget per area and hour instead of open-loop sources.** Target car count = Σ lane length /
   spacing(density) over the active area, clamped by a zone/hour cap (table like popcycle: night
   0.6, day 0.85, peaks 1.0) and a global cap. Spawning stops when at target; recycling handles
   the excess.
3. **Switched-off roads and dead-end avoidance.** Mark service roads, car parks, cul-de-sacs and
   narrow alleys as no-ambient (GTA switches off 57 % of nodes). Wander never enters a dead-end link
   unless it is the destination. Thin unobserved arterials (−1..−5 density) and boost the player's
   road.
4. **Junction box discipline.** Hold at stop nodes 12-24 m before the junction. Enter only if the
   exit link has room for the whole car (prevents spill-back locks).
   - Signals: about 10 s phases, 30-60 s cycles, 3-5 phases, with left-filter phases where
     approaches have turn lanes. Allow right-on-red (for us, the equivalent "free right") at about
     40 % of entrances. Offset neighbouring cycles for green waves.
5. **Stuck timers with hidden warp/removal.** Jammed 30 s → if unobserved, remove or warp to the
   next free node downstream; if observed, try a short reverse and lane change first.
6. **Lane change around stationary cars mid-block**, but queue (no lane change) within about 20 m of
   a stop line. On 1+1 roads, pass partly over the centre line only when the oncoming lane is clear
   for the pass length.
7. **Per-driver aggressiveness/ability (0-1)** to scale gaps, impatience timers and amber
   acceptance. This adds variety and breaks symmetric standoffs.
8. **Speed from curvature table and brake-distance band** (section 4.3) for believable approach
   speeds.
9. **Vehicle LOD / timeslicing** (real / dummy / superdummy; at most N AI updates per tick). This is
   a performance item; our flow sim already plays the superdummy role for distant cars.

---

## 8. Method, raw paths, gaps

- **Data probe**: `C:\Research\gta-traffic\probe` (CodeWalker.Core, read-only). It dumped the node
  and link graph and traffic configs to `C:\Research\gta-traffic\raw`; a copy is at
  `~/turku3d/private/gta-traffic/raw/`. Files: `nodes.csv`, `links.csv`, `junctions.csv`, `files/`
  (popcycle, popzone, zonebind, gameconfig, junctions.xml, vehicleaihandlinginfo, pedpersonality,
  vehicles/handling meta, vehiclepopulation).
  - Analysis scripts are `~/turku3d/private/gta-traffic/graph3.py` (use this one; it keeps the
    `update.rpf` node version only) and `analyse_obs.py`.
- **Scripts**: the public Enhanced 1.73 decompiled dump is at `~/turku3d/private/gta-traffic/ext/scripts`;
  nativedb is in `ext/nativedb`.
- **Observation** (`~/turku3d/private/gta-traffic/obs/`):
  - `junction_B.jsonl`: 8 min of samples every 3-5 s, all vehicles within 450 m, with a camera turn
    at 230 s;
  - `junction_A1.mp4` (CityDensity 0), `junction_B1.mp4`;
  - `block_C1.mp4` and `block_C2.mp4` (stopped car at a signal approach, 3.5 min);
  - `block_D1.mp4` (mid-block, 2 min).
- **Not obtained**:
  - **Native code**: the executable's `.text` sections are encrypted on disk (entropy 7.99) and RTTI
    is stripped (17 type names). Static Ghidra analysis was therefore not useful, so it was not
    installed.
  - Getting exact timers (impatience before overtaking, honk delay, reverse duration, removal rate
    formula, auto-junction phase rules) would need a runtime memory dump plus manual reverse
    engineering. The values in 4.4 and 5.2 marked [P/I] are therefore approximate.
  - No 10-20 min jam-dissolution recording at peak: the downtown junction did not jam at CityDensity
    1.0.
