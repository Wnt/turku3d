// Traffic watch for the clients' out-of-view recycling (game/src/traffic/flow.ts, docs/handover/gta-traffic-study.md).
// Each client may remove a long-stuck ambient car only where no player can see it, and every client must decide
// alike, so they cannot use their own view of the others (poses arrive at different times, driven ones only within
// 250 m). Once a second the server sends everyone the same list: every online player's position rounded to a metre,
// stamped with the server time of that second. Clients apply a list from WATCH.delayMs after its stamp, at the same
// traffic tick, for at most WATCH.validMs.
export const WATCH = Object.freeze({ periodMs: 1000, delayMs: 2000, validMs: 1500 });

export class TrafficWatch {
  constructor(room) { this.room = room; this.last = -Infinity; }

  tick(now) {
    const at = Math.floor(now / WATCH.periodMs) * WATCH.periodMs;
    if (at <= this.last) return;
    this.last = at;
    const points = [...this.room.players.values()].filter(p => p.online && p.connection && p.pose)
      .map(p => [Math.round(p.pose.E), Math.round(p.pose.N)]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    this.room.broadcast('watch', { at, points });
  }
}
