# Turku3D multiplayer protocol v1

Public endpoint: `wss://turku.lab.madekivi.fi/`. One room, JSON text WebSocket
messages. Every message in both directions has `"v":1` and a `"type"` string.
Unknown extra object fields are ignored. Binary messages and malformed/unknown
messages are dropped and increment `/healthz`'s `stats.rejected` counter.
No physics, collision resolution, order issuance or rendering runs on this server.

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

Money values are integer euros earned from deliveries minus 50 € for each fined broken pole in this hour, excluding
starting money, other spending, vehicle sales and the kidney payout. Ranking uses money
descending; ties have competition ranks (`1,1,3`) and sort alphabetically by nick.

`Hour` is `{id,start,end,tz}`. `start`/`end` are UTC ISO 8601 strings, start inclusive
and end exclusive; `tz` is always `"Europe/Helsinki"`. `id` is the local hour with
its offset, e.g. `2026-10-25T03+03` then `2026-10-25T03+02`. The spring transition
skips `03`. Each interval lasts 3600 elapsed seconds, even on the 23/25-hour days.

`Player` is `{id,nick,spawn,pose}`: UUID `id`, two-uppercase-letter `nick`,
`spawn:{E,N,yaw}`, and latest pose without `id`, or `null` before any pose.
`Score` is `{nick,money,deliveries,rank,online}`; `deliveries` counts paid drop-offs.
The live roster includes self; match self by `you.id`. It contains no session tokens.

## Client → server

### hello

```json
{"v":1,"type":"hello","session":"optional 64-character lowercase hex token"}
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
are radians. The server validates and relays these fields unchanged. Missing
rotation falls back to legacy yaw. Receivers slerp the chassis along the shortest
arc and interpolate wheel transforms in the same 100 ms buffer. Model corrections
are applied after chassis rotation: parked meshes need +π/2 about local Y;
starter car, scooter and truck roots already use the chassis frame.

### pose

```json
{"v":1,"type":"pose","t":12345,"E":173,"N":-252,"H":3.7,"yaw":0,"vE":1,"vN":0,"vH":0,"mode":"foot","vehicle":null,"carrying":false}
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

### delivery

```json
{"v":1,"type":"delivery","orderId":"run-uuid:order-12:stop-0","addressId":"address-slug","payout":6,"tip":8,"routeMeters":120}
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
1.3×par; failure is at 2×par. Distance does not increase base pay. The server checks
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

A client that breaks a sign pole sends `{"v":1,"type":"world","action":"break","poleId":"t0_0_0:GIS_Liikennemerkit:123","by":"own player UUID","t":12345}` followed by `{"v":1,"type":"fine","reason":"pole","poleId":"same ID","amount":50}`. Pole IDs are stable per visual tile and source object. The server accepts the first break of an ID in an hour, checks `by` against the sender and broadcasts it. A fine is accepted once for a pole broken by that player; it subtracts 50 from the hourly score, which may become negative. The local wallet is separately floored at zero. Duplicate or malformed events increment `stats.rejected`. The broken set is held in memory and cleared at hour rollover.

## Server → client

| Type | Fields in addition to `v,type` | When / behavior |
|---|---|---|
| `welcome` | `session`, `you:{id,nick,spawn}`, `hour:Hour`, `players:Player[]`, `scores:Score[]`, `brokenPoles:{action,poleId,by,t}[]` | After hello; also after each hour reset to communicate the new nickname. |
| `players` | `players:Player[]` | Full replacement roster after join, leave, idle/resume or reset. Remove absent remote bodies. |
| `poses` | `poses:[{id,t,E,N,H,yaw,vE,vN,vH,mode,vehicle,carrying}]` | Every 50 ms when changes exist: latest accepted pose per changed player, excluding recipient. Omitted players have no new pose; omission does not remove them. |
| `world` | `action:"break",poleId,by,t` | Re-broadcast of an accepted pole break. |
| `scores` | `scores:Score[]` | Full replacement scoreboard after every accepted delivery or fine and presence change/join/reset. Includes idle/disconnected participants from this hour. |
| `hour_end_in` | `seconds` | Once in the last 10 seconds (normally 10; lower if the event loop was delayed). |
| `hour_reset` | `hour:Hour` | Old hour archived; switch the UI to the new hour, clearing previous scores. Followed by welcome and roster/scores. |
| `error` | `code:"protocol",reload:true` | Missing/unsupported `v` in a JSON object; close 1002 follows. Reload a matching client. |
| `error` | `code:"nicknames",reload:false` | All available nicknames used in this hour; close 1013 follows. Retry next hour. |

Nicknames are randomly chosen from A–Z pairs except `SS,SA,HH,KK,FU`. Names stay
reserved throughout an hour, including disconnected participants (671 possible).
At rollover, only connected sessions continue, with money/deliveries zeroed and a
different random nickname per player. Their ID/token/spawn/pose and paid-order IDs
stay intact; idle connections stay idle. Disconnected sessions expire at rollover.
Spawns rotate through twelve fixed street-side positions in join order, repeating
after twelve new players; reconnect preserves the assigned point. The client snaps
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
| `/healthz` | `{ok:true,v:1,hour,connections,online,stats:{rejected,deliveries,poses,bytesIn,bytesOut}}` |
| `/live.json` | `{hour:Hour,players:Player[],scores:Score[]}` |
| `/scores/index.json` | `Hour[]`, finished hours newest first by UTC start (including both autumn hours) |
| `/scores/<id>.json` | `{hour:Hour,players:[{nick,money,deliveries,rank}]}`; missing/invalid ID 404 |

Byte counters measure application JSON payload, excluding WebSocket/TCP/TLS framing.
Counters are process totals. Finished hours are written atomically to
`server/data/scores/`, without IDs/tokens/poses. Files whose end is at least seven
days old are pruned on startup/first tick and each new Finnish calendar day.
A process restart preserves finished files but loses the unfinished in-memory hour,
sessions and deduplication. No empty hours are backfilled for server downtime.
