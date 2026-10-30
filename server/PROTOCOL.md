# Turku3D multiplayer protocol v2

Public endpoint: `wss://turku.lab.madekivi.fi/`. One room, JSON text WebSocket
messages. Every message in both directions has `"v":2` and a `"type"` string.
Unknown extra object fields are ignored. Binary messages and malformed/unknown
messages are dropped and increment `/healthz`'s `stats.rejected` counter.
The server owns vehicle seats/leases and computes bounded car-contact impulses from replicated poses.
Rapier simulation, terrain collision, order issuance and rendering remain client-side.
Version 1 tabs must reload the Pages game: unsupported versions receive `error` with
`reload:true` and close 1002. Version 2 clients stop retrying and display a reload notice
on this error (also when it comes from an older server during deployment).

## Coordinates, time and money

`E` and `N` are **local east and north metres**, relative to EPSG:3877 origin
`(23459500, 6704500)`. Convert engine position with `E=x`, `N=-z`, `H=y`;
convert absolute map coordinates by subtracting that origin. Do not send the
absolute EPSG numbers. `H` is N2000 elevation in metres at the character's feet
in `foot` mode. In `car`, `scooter` and `truck` modes it is the chassis centre minus 0.6 m;
the receiver adds 0.6 m for the visual root and kinematic chassis collider. This
keeps the network pose near ground level while preserving the local vehicle's
chassis origin. `vE`, `vN`, `vH` are
metres/second on those same axes. `yaw` is radians about engine +Y, forward -Z
at zero: `yaw=-headingDegrees*pi/180`. Send yaw normalized to `[-2π,2π]`, using
the actor's facing direction (the client must account for its model's forward axis).

`t` is the client's monotonic timestamp in milliseconds (e.g. `performance.now()`),
not trusted server time. It is echoed for interpolation, never used for payouts or
hour assignment. The client must handle a new timestamp epoch after rejoin.

Earned cash is the sum of validated positive delivery pay, tips and bonuses in this
hour. It is separate from the local wallet: fines, purchases and other spending never
reduce it. Starting cash, developer grants and asset sales are not earnings. Future
reward events must be validated server-side before contributing to this total.
Ranking uses earned cash descending, then paid deliveries descending, then the earliest
server time at which that result was reached (join order resolves simultaneous times).
Ranks are sequential. Open multiplayer tabs must reload after this score schema update.

`Hour` is `{id,start,end,tz}`. `start`/`end` are UTC ISO 8601 strings, start inclusive
and end exclusive; `tz` is always `"Europe/Helsinki"`. `id` is the local hour with
its offset, e.g. `2026-10-25T03+03` then `2026-10-25T03+02`. The spring transition
skips `03`. Each interval lasts 3600 elapsed seconds, even on the 23/25-hour days.

`Player` is `{id,nick,spawn,pose}`: UUID `id`, two-word CamelCase `nick` (friendly adjective + noun, ASCII letters, at most 16 characters,
e.g. `TurboHerring` or `SneakyPizza`),
`spawn:{E,N,yaw}`, and latest pose without `id`, or `null` before any pose.
`Score` is `{nick,earned,deliveries,rank,online}`; `deliveries` counts paid drop-offs.
The live roster includes self; match self by `you.id`. It contains no session tokens.

## Client → server

### hello

```json
{"v":2,"type":"hello","session":"optional 64-character lowercase hex token"}
```

Send once within 5 seconds of connecting; omit `session` on first join. A current
hour token rejoins the same ID, nickname, spawn, score and paid-order set. It
replaces any earlier socket using that token (old socket closes with code 4001).
An unknown but well-formed token starts a new player; malformed tokens are rejected.
The token is a bearer credential; retain it privately, never put it in a URL.

### Vehicle orientation

Vehicle poses may include `rot:{x,y,z,w}`, a unit chassis quaternion in engine
axes, and `wheels:[{pos:{x,y,z},steer,rotation,contact,suspension}]` (up to four).
Wheel positions are chassis-local metres; steering and unwrapped wheel rotation
are radians. The server validates these fields, strips unknown nested fields and rounds motion values to 0.001 before relaying them. Missing
rotation falls back to legacy yaw. Receivers slerp the chassis along the shortest
arc and interpolate wheel transforms in the same 100 ms buffer. Model corrections
are applied after chassis rotation: parked meshes need +π/2 about local Y;
starter car, scooter and truck roots already use the chassis frame.

Each wheel may also carry `tyre:{point:{x,y,z},normal:{x,y,z},material,lateral,longitudinal}`.
Point and normal use the physical chassis frame; slip components are m/s. This optional
wheel metadata is bounded and relayed with the same 0.001 precision. Consumers validate finite,
bounded contact data and gate marks/smoke on contact, known hard materials and sliding
speed. Missing metadata produces no tyre effects. Interpolated chassis transforms also
transform the contact data; visual model orientation corrections do not apply to it.

### pose

```json
{"v":2,"type":"pose","t":12345,"E":173,"N":-252,"H":3.7,"yaw":0,"vE":1,"vN":0,"vH":0,"mode":"foot","vehicle":null,"carrying":false}
```

All fields required. Numbers must be finite: `0≤t≤Number.MAX_SAFE_INTEGER`,
`|E|,|N|≤100000`, `|H|≤10000`, `|yaw|≤2π`, each velocity component ≤200 in absolute
value. `mode` is `"foot"`, `"car"`, `"scooter"` or `"truck"`; `carrying` is boolean.
`vehicle` is null on foot, `"scooter"` on a scooter, `"truck"` in the trophy truck, `"player"` in the starter car,
or `<model>/<colour>` in a driven parked car (the key in `manifest.vehicle_models`).
It changes with the driven vehicle and lets receivers use its exact mesh and paint. These are
sanity bounds, not server physics or movement verification. Send at up to 20 Hz,
including stationary poses while actively playing. Sixty seconds without an
accepted pose removes the player from the live roster and sets `online:false`;
the score remains. A new valid pose restores presence. A join starts the idle timer.

### Shared vehicles and seats

A `SharedVehicle` is `{id,source,creator,localId,owner,occupant,epoch,revision,half,pose}`.
`pose` uses the chassis convention above, including model/paint in `vehicle`, kind
in `mode`, quaternion, velocity and wheels. `half:{x,y,z}` is the physical chassis
half extent (0.1–4 m per axis); masses are fixed per kind: car 1200 kg, scooter
190 kg, truck 2400 kg. `occupant` is one player UUID or null; `owner` is the sole
simulation's UUID. `epoch` changes on ownership transfer; `revision` changes on state.

Parked source IDs are manifest `object_id`s; registry IDs are `park:<object_id>`.
Personal IDs are `<creator UUID>:<localId>` (`player`, `scooter`, `truck`). The
server validates this namespace. `source` is null for personal vehicles. A source
reservation suppresses its original parked actor for every client, including clients
outside the vehicle's current interest area. No client may enter before a successful
server response. One player can hold one seat; a simultaneous second claim loses.

```
{v:2,type:"vehicle",action:"register"|"acquire",request,id,source,localId,half,pose}
{v:2,type:"vehicle",action:"acquire"|"release",request,id}
{v:2,type:"vehicle_result",request,ok,vehicle:SharedVehicle|null,reason?:"seat_taken"}
```

`request` and IDs use `[A-Za-z0-9_.:-]`, maximum 180 characters. A new record must
be within 150 m of the sender; acquisition within 7 horizontal metres. Existing
records keep their model, source and size regardless of a competing registration.
Registration makes a moved car visible without occupying it. Acquiring an empty
vehicle transfers simulation; release keeps its current simulation with the last
driver. Connected holders keep their seats regardless of pose idle time (including
long loading stalls). Disconnect zeros retained velocities and reserves occupied
seats for five minutes; reconnect/socket replacement preserves the holder and epoch.
Only explicit release or departure beyond that reservation frees a seat. Re-acquiring
one's own seat bypasses the proximity check. A competing acquisition returns
`ok:false,reason:"seat_taken"` and the current holder; other denials omit the reason.
Clients reclaim the driven seat after welcome, preserve their live body through
empty/missing registry snapshots, and only exit on this explicit conflict after
three seconds of grace, at the vehicle's current driver side. Recovered ownership
cancels the exit. Clients restore an owned record's full pose,
including flipped rotation, rather than creating another car in the initial slot.

A pose message can include `vehicleId` for the occupied registry vehicle (the server
checks its seat/owner/kind), and `vehicles:[{id,epoch,pose}]`, at most eight changed
owned records. Old-owner/stale-epoch writes are rejected. Unchanged resting vehicles
produce no updates, regardless of player heartbeats; client dirty thresholds are
1 cm, 0.001 quaternion components and 0.01 wheel radians. Register/acquire/release,
deliveries and pole/fine events replace pose sends in the same 55 ms client scheduler.
No extra contact-request stream consumes the 20-message/s allowance.

A `VehicleDelta` is `{upsert:SharedVehicle[],remove:string[],sources?:string[],
patches?:[{id,revision,pose:Partial<Pose>}]}`. `welcome.vehicles` supplies a full
nearby/owned snapshot and all reserved source IDs. Later `vehicles` messages contain
changes only. Apply removals, upserts and shallow pose patches; omitted fields are
retained. Metadata changes use full upserts. Owner motion is not echoed to its own
simulation. A replica outside 250 m is removed; entering the radius sends a full
record. Reserved sources are global, at most 256 IDs, so streaming cannot resurrect
an absent original slot. Every existing registered vehicle, occupied or empty,
is rendered when its collision area is loaded.

Retention: eight records created per player, 256 room-wide. Creating a ninth evicts
the oldest unoccupied record from that creator (an occupied record is never evicted).
Empty records expire one hour after last seat use. Expiry/removal releases their
original parked slots. Records survive score-hour rollover but live only in memory;
a server restart clears the registry. This is bounded session state, not persistence.
Disconnected owners' empty records also survive the five-minute reconnect grace,
even at the end of their lifetime. A retained-session hello renews the lifetime of
its owned records. The session token stored by the tab identifies the player across
reloads; a new socket does not create a new owner. A fresh client restores its
registry bodies and occupied seat before publishing poses.

### Shared contact response

Every 50 ms, the server checks nearby vehicle pairs owned by different simulations.
Swept horizontal chassis boxes look ahead at most 100 ms; height separation excludes
bridge/road pairs. A closing contact produces an equal/opposite impulse using the
class masses and restitution 0.05. Magnitude is capped at 12000 N·s and at 8 m/s
velocity change for the lighter participant. A 250 ms per-pair **and per-vehicle**
cooldown bounds a pile-up. Moving state older than 1.5 s cannot initiate a contact.

```
{v:2,type:"vehicle_impulse",id,epoch,sequence,E,N}
```

Only owners receive these impulses. Clients deduplicate by sequence and epoch,
apply them once at the chassis centre without lift/torque, and enforce the same
magnitude/delta-v limit in the worker. Empty targets transfer to the hitter before
impulses are sent, including when their previous owner is online but paused/far away.
The new owner first receives the full pre-impulse pose, then both impulse halves.
Two bodies already in one simulation use Rapier's normal dynamic mass solver.

Remote proxies collide with the walking capsule only: they cannot inject infinite
kinematic mass into local vehicles or wheel queries. This is a bounded client-physics
contact approximation, not authoritative terrain simulation or rollback netcode;
extreme latency/high-speed interpenetration is a remaining limitation.

### delivery

```json
{"v":2,"type":"delivery","orderId":"run-uuid:order-12:stop-0","addressId":"address-slug","payout":6,"tip":8,"routeMeters":120}
```

One message per **paid drop-off**. Use a unique ID per stop (double orders have two
IDs). `orderId` and `addressId` are 1–128 characters from `[A-Za-z0-9_.:-]`.
Generate an order/run UUID so page reloads do not reuse IDs. `payout` is base pay
plus any double bonus, **excluding** `tip`. `routeMeters` is finite and in
`[0,100000]`; the 100 km ceiling is a protocol sanity limit, not a game formula.

The game's formula is `6 + tip + bonus`: integer tip 0–8; bonus 4 only on a second
drop-off with tip >0. Thus `payout` must be 6, or 10 with tip >0; total maximum 18.
Distance determines par time (`distance / speed + 20`, speeds foot=1.4,
scooter=7, car=9, truck=10.5 m/s). Tip rounds a clamped linear fall from 8 at 0.7×par to 0 at
1.3×par; failure is at 2×par. Long jobs add optional `job: "long"`; omitted means `"short"`. Their base payout is
`24 + ceil(routeMeters * 0.012)` with 60 < routeMeters <= 7000 and no double bonus.
Long-job par speed is 1.4 times the short speed (1.2 for scooters); the same tip and failure fractions apply. The server checks
the possible euro envelope; this message cannot prove timing, ownership, route,
address existence, second-stop status, or that a delivery actually happened.

Duplicate order IDs are rejected per player/session, including reconnects and
connected hour resets. There is a 10000-ID cap per continuing session; further
claims are rejected. At most 20 total messages/second means delivery messages
must replace a pose transmission in a full-rate stream. Accepted delivery emits
`scores`; rejected delivery emits nothing and increments `stats.rejected`.
An order crossing the hour boundary counts in the hour when the server receives
its delivery, regardless of `t` or when the client started the order.

### world break and fine

A client that breaks a sign pole sends `{"v":2,"type":"world","action":"break","poleId":"t0_0_0:GIS_Liikennemerkit:123","by":"own player UUID","t":12345}` followed by `{"v":2,"type":"fine","reason":"pole","poleId":"same ID","amount":50}`. Pole IDs are stable per visual tile and source object. The server accepts the first break of an ID in an hour, checks `by` against the sender and broadcasts it. A fine is accepted once for a pole broken by that player; it records the fine without changing earned cash or leaderboard rank. The local wallet is separately floored at zero. Duplicate or malformed events increment `stats.rejected`. The broken set is held in memory and cleared at hour rollover.

## Server → client

| Type | Fields in addition to `v,type` | When / behavior |
|---|---|---|
| `welcome` | `session`, `you:{id,nick,spawn}`, `hour:Hour`, `players:Player[]`, `scores:Score[]`, `brokenPoles:{action,poleId,by,t}[]`, `vehicles:VehicleDelta`, `serverTime` (UTC epoch ms) | After hello; also after each hour reset to communicate the new nickname. |
| `players` | `players:Player[]` | Full replacement roster after join, leave, idle/resume or reset. Remove absent remote bodies. |
| `poses` | `poses:[{id,t,E,N,H,yaw,vE,vN,vH,mode,vehicle,carrying}]` | Every 50 ms when changes exist: latest accepted pose per changed player, excluding recipient. Driven poses are limited to recipients within 250 m. Omitted players have no new pose; omission does not remove them. |
| `world` | `action:"break",poleId,by,t` | Re-broadcast of an accepted pole break. |
| `scores` | `scores:Score[]` | Full replacement scoreboard after every accepted delivery or fine and presence change/join/reset. Includes idle/disconnected participants from this hour. |
| `hour_end_in` | `seconds` | Once in the last 10 seconds (normally 10; lower if the event loop was delayed). |
| `hour_reset` | `hour:Hour` | Old hour archived; switch the UI to the new hour, clearing previous scores. Followed by welcome and roster/scores. |
| `error` | `code:"protocol",reload:true` | Missing/unsupported `v` in a JSON object; close 1002 follows. Reload a matching client. |
| `error` | `code:"nicknames",reload:false` | All available nicknames used in this hour; close 1013 follows. Retry next hour. |

Nicknames are random friendly adjective + noun combinations in CamelCase. The
curated pool has 6,552 names of at most 16 ASCII letters. Names stay reserved
throughout an hour, including disconnected participants. At rollover, only
connected sessions continue, with earned/deliveries zeroed and a different random
nickname per player. Their ID/token/spawn/pose and paid-order IDs stay intact;
idle connections stay idle. Disconnected sessions expire at rollover. Archived
hours retain their original nicknames, including legacy two-letter names.
Spawns rotate through sixteen fixed street-side positions in join order, repeating
after sixteen new players; reconnect preserves the assigned point. The client snaps
the feet to ground and handles temporary occupation by other players. A reset's
welcome is an identity update, not a request to teleport; on rejoin use the retained
self pose when present, otherwise spawn.

## Transport limits

- Maximum 32 simultaneous WebSockets, including connections awaiting hello and
  closing sockets; additional upgrades receive HTTP 503.
- Acceptance uses a token bucket replenished at 20 messages/second, capacity 20,
  counting hello, malformed messages and deliveries too. Messages arriving with
  no token are dropped and counted. Independently, the 26th arrival in a sliding
  1000 ms window closes with 1008 (`rate`), including rejected arrivals. This allows
  small timing jitter and bursts of at most 20 while limiting sustained acceptance
  to 20/s. Budget normal traffic at ≤20 total/s.
- Maximum message size 65536 bytes, including fragmented messages. Oversize closes
  with 1009. Compression is disabled.
- Uninitialized connections close after 5 seconds (1008). Server ping every 30 s;
  a missing pong at the next ping terminates the connection. Browser WebSocket
  implementations answer pings automatically.
- Slow readers with more than 256 KiB queued output are disconnected.
- Upgrades require path `/` and an exact Origin of `https://wnt.github.io` or
  `http://localhost` with an optional valid nonzero port. Missing Origin, `null`,
  `127.0.0.1`, other hosts, schemes, paths and subdomains receive HTTP 403.
  Non-browser clients such as the probe must set Origin explicitly.

## HTTP and retention

Same public host over HTTPS; local bind `127.0.0.1:7700`. GET requests without Origin
work for curl. Browser requests use the same Origin allowlist (403 otherwise);
allowed origins receive `Access-Control-Allow-Origin` and `Vary: Origin`. OPTIONS
returns 204; unsupported methods 405. JSON responses use `Cache-Control: no-store`.

| Path | JSON response |
|---|---|
| `/healthz` | `{ok:true,v:2,hour,connections,online,stats:{rejected,deliveries,poses,bytesIn,bytesOut}}` |
| `/live.json` | `{hour:Hour,players:Player[],scores:Score[]}` |
| `/scores/index.json` | `Hour[]`, finished hours newest first by UTC start (including both autumn hours) |
| `/scores/<id>.json` | `{hour:Hour,players:[{nick,earned,deliveries,rank}]}`; missing/invalid ID 404 |

Byte counters measure application JSON payload, excluding WebSocket/TCP/TLS framing.
Counters are process totals. Finished hours are written atomically to
`server/data/scores/`, without IDs/tokens/poses. Files whose end is at least seven
days old are pruned on startup/first tick and each new Finnish calendar day.
A process restart preserves finished files but loses the unfinished in-memory hour,
sessions and deduplication. No empty hours are backfilled for server downtime.

Legacy hourly files with `money` instead of `earned` remain readable. Their recorded
net totals and ranks are preserved; gross earnings cannot be recovered from those files.

## Timed rounds

`welcome.rounds` and `rounds {rounds, serverTime}` carry `{epoch, proposal, active, history}`.
The proposal has a UUID `id`, `initiator` player ID, `nick`, connected voter IDs in `votes`,
`needed = ceil(connected/2)` and `connected`. Hello-complete sockets count even in a menu.
Disconnecting the initiator cancels the proposal; other departures recalculate its threshold.

Clients send `round {action: "propose"}` then `round {action: "agree", proposal: ID}`.
Only the initiator may send `round {action: "start", proposal: ID, minutes: N}` after
half agree. N is 5–180 in five-minute steps. The server assigns a new `epoch`, wipes
scores, order/fine deduplication, broken poles, all vehicle records and retained player
poses, and sends fresh welcomes with existing identities and assigned spawn positions.
The client clears gameplay (including purchased vehicles and missions) to the new-game
baseline, including the €120 starting wallet needed to rent a delivery permit. World
geometry, streamed tiles and asset caches stay resident.

`active` has `{id, initiator, minutes, phase, start, end}`; deadlines use server Unix ms.
`phase` is `countdown` for five seconds, then `running`. Gameplay messages carry the
welcome `epoch`; stale epochs and all gameplay during countdown are rejected. Normal
clock-hour resets and warnings are suppressed throughout countdown/running. Late joins
receive current deadlines and the same state. A replacement round uses the same vote.

`round_result {result}` broadcasts the frozen ranked scores on timeout; restarting archives
the interrupted result with `reason: "restarted"`. A result also has `finished`, `scores`
and `phase: "finished"`. Clients show timeout results with a dismiss action and keep the
last 20 results in the pause menu. These results persist atomically in `rounds.json` across
server restarts; in-progress rounds remain in memory. On finish normal play continues,
with the next hourly reset at the next Helsinki hour boundary; missed resets are skipped.
