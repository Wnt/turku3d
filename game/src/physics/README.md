
## Breakable traffic-sign poles

The props producer emits one `pole_id` per sign and bus-stop node (`<visual tile>:<source object id>`). It omits their baked collision primitives. `world/poles.ts` mirrors resident nodes into worker-owned sleeping dynamic cylinders (2.7 m high, 0.05 m physics radius), fixed to a ground anchor by a Rapier fixed joint. The joint is removed when the driven car has a step impact of at least 0.8 m/s step speed change within 2.2 m of the pole, or a foot push at 2.6 m/s within 0.38 m. The body topples for 20 simulated seconds and then becomes fixed debris. The worker keeps the broken-ID set across tile eviction; the play session keeps it across visual streaming. `setPoles` adds and removes residents, and `breakPole` applies a remote break.
