import { randomUUID } from 'node:crypto';
import { hourAt } from './hours.js';
import { VehicleRegistry } from './vehicles.js';

export const COUNTDOWN_MS = 5000;
/** Server clock, electorate and results; no client may supply a deadline or score. */
export class TimedRounds {
  constructor(room) {
    this.room = room;
    this.proposal = null;
    this.active = null;
    this.history = room.store.rounds?.() ?? [];
    this.epoch = null;
  }
  connected() { return [...this.room.players.values()].filter(p => p.connection); }
  snapshot() {
    const connected = this.connected();
    const votes = this.proposal ? connected.filter(p => this.proposal.votes.has(p.id)).map(p => p.id) : [];
    return { epoch: this.epoch, active: this.active, history: this.history,
      proposal: this.proposal ? { id: this.proposal.id, initiator: this.proposal.initiator,
        nick: this.proposal.nick, votes, needed: Math.ceil(connected.length / 2), connected: connected.length } : null };
  }
  publish() { this.room.broadcast('rounds', { rounds: this.snapshot(), serverTime: this.room.now() }); }
  membership() {
    if (this.proposal && !this.connected().some(p => p.id === this.proposal.initiator)) this.proposal = null;
    this.publish();
  }
  command(p, m, now) {
    const fail = () => { this.room.reject(); this.room.send(p.connection, 'error', { code: 'round_request' }); };
    if (m.action === 'propose' && !this.proposal && this.active?.phase !== 'countdown') {
      this.proposal = { id: randomUUID(), initiator: p.id, nick: p.nick, votes: new Set([p.id]) };
    } else if (m.action === 'agree' && this.proposal && m.proposal === this.proposal.id) {
      this.proposal.votes.add(p.id);
    } else if (m.action === 'start' && this.proposal && m.proposal === this.proposal.id
      && p.id === this.proposal.initiator && Number.isInteger(m.minutes) && m.minutes >= 5 && m.minutes <= 180 && m.minutes % 5 === 0
      && this.snapshot().proposal.votes.length >= this.snapshot().proposal.needed) {
      if (this.active) this.finish(now, 'restarted');
      this.epoch = randomUUID();
      this.active = { id: this.epoch, minutes: m.minutes, phase: 'countdown', start: now + COUNTDOWN_MS,
        end: now + COUNTDOWN_MS + m.minutes * 60000, initiator: p.nick };
      this.proposal = null;
      this.wipe(now);
    } else { fail(); return; }
    this.publish();
  }
  wipe(now) {
    const r = this.room;
    r.vehicles = new VehicleRegistry(r);
    r.brokenPoles.clear();
    for (const p of r.players.values()) {
      p.earned = 0; p.deliveries = 0; p.reachedAt = now;
      p.orders.clear(); p.finedPoles.clear(); p.pose = null; p.dirty = false;
    }
    r.warned = false;
    for (const c of r.connections) if (c.player) r.welcome(c);
    r.presence();
  }
  finish(now, reason) {
    const result = { ...this.active, phase: 'finished', finished: now, reason, scores: this.room.scores() };
    this.history = [result, ...this.history].slice(0, 20);
    this.room.store.writeRounds?.(this.history);
    this.active = null;
    // Skip every clock-hour boundary suppressed by the round. Normal play resumes in this hour.
    this.room.hour = hourAt(now);
    this.room.warned = false;
    this.room.broadcast('round_result', { result });
  }
  tick(now) {
    if (!this.active) return;
    if (now >= this.active.end) { this.finish(this.active.end, 'time'); this.publish(); }
    else if (this.active.phase === 'countdown' && now >= this.active.start) {
      this.active.phase = 'running'; this.publish();
    }
  }
}
