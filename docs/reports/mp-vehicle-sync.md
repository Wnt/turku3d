# Shared vehicles and 16-player synchronization audit

2026-10-29. No screenshots, Chrome sessions or driving checks; Jonni owns playtesting.

## Delivered

- Protocol v2 registry retains moved/occupied starter cars, borrowed parked cars,
  scooters and trucks with model/paint, full pose, owner and one occupant.
- Atomic seats prevent two drivers; globally reserved source IDs suppress the
  original parked slot even outside the vehicle's interest radius. Acquired foreign
  vehicles retain their mesh/paint and use the new owner's physics pose.
- Release, disconnect and late join retain cars. Reconnect restores their quaternion
  and velocity; a flipped car stays flipped. Recovery transforms replicate as poses.
- Each vehicle has one simulation owner and an epoch fencing old writers. Contact
  transfers empty targets to the hitter, including online owners paused or far away.
- Different-owner driven contacts use swept boxes, class masses and equal/opposite
  impulses, capped at 12000 N·s and 8 m/s delta-v per lighter body. Per-pair and
  per-vehicle 250 ms cooldowns bound pile-ups. No lift/torque is injected.
- Remote colliders block walking only and cannot launch a local vehicle or support
  its wheels. Same-owner dynamic bodies retain Rapier's normal mass solver.
- One 55 ms client scheduler budgets every message, including pole/fine pairs.
  At most eight changed owned records ride with a pose. Registry data is limited
  to 250 m plus own vehicles; resting cars emit nothing. Shallow motion patches
  avoid repeated metadata; owner motion is not echoed. Unknown nested fields are
  stripped and motion is rounded to 0.001 before relay.
- Eight retained vehicles per creator, oldest empty eviction, one-hour empty expiry,
  256 room cap. Expiry releases original slots; active occupied cars are retained.
- Cheap audit fixes: visible carried pizza boxes, welcome-based server-clock alignment,
  sixteen distinct character spawn points, reload notice on incompatible protocol,
  late-join source reservations and full orientation restoration.

## Evidence

Quick headless coverage checks atomic claims, invalid owner/epoch writes, source
suppression outside interest, resting silence, removal/expiry/caps, late join,
reconnect, quaternion/velocity transfer, unequal masses and both impulse halves,
swept misses on adjacent lanes/overpasses, bounded real-Rapier impulse application,
remote collider tunnelling without a launch, render persistence/paint after a driver
leaves, and the client's combined transport rate. The WebSocket vehicle probe uses
real sockets and covers claim race, release, late join, transfer and reconnect.

Development load samples used 32 sockets, 16 drivers at 20 Hz, 112 resting vehicles
and four articulated wheels per driver. The final three-second sample accepted all
928 pose messages with zero rejections, all 32 sockets connected, about 0.30 MB/s
server input and 3.30 MB/s output (JSON payload; ~0.21 MB/s per nearby player), and
16.85% of one Mac CPU core. All parked records were silent after registration.
These are synthetic transport measurements on the Mac, not OCI latency or visual
collision-fidelity measurements. The first frozen-source 30-second run accepted all 9,264 poses with zero rejections,
32 connections retained, 3.35 MB/s output and 25.45% of one Mac core. Its following
lifecycle probe initially failed because the frozen work directory did not yet exist;
the forward fix makes both tools create their own work root before temporary files.
The delivery receipt owns the repeated post-deploy result.

## Remaining shared-city work, in priority order

Sizes: S = up to half a day; M = 1–3 days; L = roughly a week or more.

| Priority | Gap | Size / next action |
|---|---|---|
| P1 | High-latency/high-speed driven-car interpenetration; swept horizontal boxes approximate pitched/flipped chassis. Impulses are bounded but this is not authoritative collision prediction or rollback. | M — buffered contact reconciliation/position correction, with latency/jitter fixtures and Jonni's playtest evidence. |
| P1 | Personal starter/dealer vehicle spawn placement can overlap another actor; 16 character spawns are distinct, but dynamic obstruction is not server-reserved. | M — reserve collision-backed vehicle bays and validate occupancy before purchase/recovery placement. |
| P1 | Character–character and character–remote-car contact remains local KCC blocking; cars do not receive authoritative pedestrian hit/push/ragdoll events. | M — shared hit events and bounded character response; remote walking proxies intentionally cannot launch cars. |
| P1 | Vehicle/session state and unfinished scores are in-memory and lost on server restart. Connected-hour rollover preserves vehicles, but restart does not. | M — persist registry with epoch/session reconciliation and expiry; completed hourly scores already persist. |
| P1 | Order issuance, carried order contents, targets, deadline/route ownership, shop/pickup reservations, wallet/purchases and delivery proof remain local. Scores and carried/not-carried appearance are shared. | L — server-issued orders and economy transactions, with optional teammate target visibility. |
| P2 | Remote engine/horn/light/siren/brake audio and switch state are absent. Horn/light controls have no current network event contract. Local crash feedback and remote tyre effects already work. | S for spatial engine/horn/light events; M for event lifetime, interest and mute policy. |
| P2 | Recovery/teleport pose is shared, but there is no reset reason, animation, cooldown or safe shared landing reservation. | M — authoritative recovery request plus reserved clear landing pose. |
| P2 | Broken pole IDs/fines are shared, but precise fallen-pole rigid-body trajectories and other breakable props are not. Pole hour-reset/client restoration also needs an explicit world-state lifecycle. | M — generalized prop events, retained final states and reset handling. |
| P2 | Walking/riding animation state, jumps, gestures, held-object attachment and remote seated riders are approximate; no character health/ragdoll state exists. | M — compact action/animation state tied to shared actors. |
| P2 | Weather is not a shared changing simulation. The day cycle now uses server time, but explicit developer clock overrides and resumed/pinned clocks remain local. | S — room weather/time settings if these become gameplay controls. |
| P2 | No movement/teleport/vehicle-source authenticity checks against generated world data; registry bounds are sanity/ownership checks, not anti-cheat. | L — server collision/world validation and authoritative movement/economy. |
| P3 | No shared AI traffic, pedestrians, traffic-signal phases, doors or mutable shop inventory exist yet. | L — authoritative entity/event lifecycle when these systems are introduced. |
| P3 | Registry vehicles beyond interest have no moving minimap update until re-entry; owner-free empty vehicles remain frozen until contact/acquisition. | S/M — optional low-rate coarse map presence and ownership reassignment policy. |

Old v1 tabs must reload the newly published Pages game. Protocol mismatch closes
1002 rather than allowing incompatible simulations to interact. Updated clients
show a reload message and stop retrying on a version mismatch.
