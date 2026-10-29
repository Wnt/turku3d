import { validPose, cleanPose } from './pose.js';
import { contactImpulse, TUNING } from './vehicle-contact.js';

export const VEHICLE_LIMITS = { perPlayer: 8, total: 256, lifetime: 3_600_000, reconnectMs: 300_000, radius: 250, updates: 8 };
const idOK = id => typeof id === 'string' && /^[A-Za-z0-9_.:-]{1,180}$/.test(id);
const distance = (a, b) => Math.hypot(a.E - b.E, a.N - b.N);
const publicVehicle = v => ({ id: v.id, source: v.source, creator: v.creator, localId: v.localId,
  owner: v.owner, occupant: v.occupant, epoch: v.epoch, revision: v.revision, half: v.half, pose: v.pose });

/** Ownership/seat arbitration, bounded retention and interest-filtered deltas. */
export class VehicleRegistry {
  constructor(room) { this.room = room; this.entries = new Map(); this.contacts = new Map(); this.serial = 0; }
  changed(v) { v.revision = ++this.serial; }
  nearby(v, p) { return v.owner === p.id || distance(v.pose, p.pose ?? p.spawn) <= VEHICLE_LIMITS.radius; }
  snapshot(c) {
    const visible = [...this.entries.values()].filter(v => this.nearby(v, c.player));
    c.vehicleVersions = new Map(visible.map(v => [v.id, v.revision]));
    c.vehicleSent = new Map(visible.map(v => [v.id, publicVehicle(v)]));
    // Source reservations are tiny and global: streaming the original tile must
    // never resurrect a car whose current position is outside the interest radius.
    c.vehicleSources = this.sources();
    return { upsert: visible.map(publicVehicle), remove: [], sources: c.vehicleSources };
  }
  sources() { return [...this.entries.values()].flatMap(v => v.source ? [v.source] : []); }
  command(p, m, now) {
    const reply = (ok, v = null, reason) => this.room.send(p.connection, 'vehicle_result', {
      request: m.request, ok, vehicle: v ? publicVehicle(v) : null, ...(reason ? { reason } : {}) });
    if (!idOK(m.id) || !idOK(m.request) || !['register', 'acquire', 'release'].includes(m.action)) {
      this.room.reject(); reply(false); return;
    }
    let v = this.entries.get(m.id);
    if (!v && m.action !== 'release') {
      const source = m.source ?? null;
      if (!validPose(m.pose) || m.pose.mode === 'foot' || !idOK(m.localId)
        || (source !== null && (!idOK(source) || m.id !== `park:${source}`))
        || (source === null && m.id !== `${p.id}:${m.localId}`)
        || distance(m.pose, p.pose ?? p.spawn) > 150
        || this.entries.size >= VEHICLE_LIMITS.total) { this.room.reject(); reply(false); return; }
      const half = m.half ?? Object.fromEntries(['x', 'y', 'z'].map((k, i) => [k, TUNING[m.pose.mode].half[i]]));
      if (!['x', 'y', 'z'].every(k => Number.isFinite(half[k]) && half[k] >= 0.1 && half[k] <= 4)) {
        this.room.reject(); reply(false); return;
      }
      const own = [...this.entries.values()].filter(v => v.creator === p.id);
      if (own.length >= VEHICLE_LIMITS.perPlayer) {
        const oldest = own.filter(v => !v.occupant).sort((a, b) => a.used - b.used)[0];
        if (!oldest) { reply(false); return; }
        this.entries.delete(oldest.id);
      }
      v = { id: m.id, source, localId: m.localId, creator: p.id, owner: p.id, occupant: null,
        pose: cleanPose(m.pose), half, epoch: 1, updated: now, used: now, revision: 0, impulseAt: -Infinity };
      this.entries.set(v.id, v); this.changed(v);
    }
    if (!v) { reply(false); return; }
    if (m.action === 'acquire') {
      if (v.occupant && v.occupant !== p.id) { reply(false, v, 'seat_taken'); return; }
      // A retained driver may have travelled far beyond their last player pose.
      // Reclaiming their own seat is idempotent, including after socket replacement.
      if ((v.occupant !== p.id && distance(v.pose, p.pose ?? p.spawn) > 7)
        || [...this.entries.values()].some(other => other.id !== v.id && other.occupant === p.id)) { reply(false, v); return; }
      if (v.owner !== p.id) { v.owner = p.id; v.epoch++; }
      v.occupant = p.id; v.used = now; this.changed(v);
    } else if (m.action === 'release') {
      if (v.occupant !== p.id) { reply(false, v); return; }
      v.occupant = null; v.used = now; this.changed(v);
    }
    reply(true, v);
    this.flush(now, false);
  }
  update(p, updates, now) {
    if (!Array.isArray(updates) || updates.length > VEHICLE_LIMITS.updates) return false;
    let valid = true;
    for (const u of updates) {
      const v = this.entries.get(u?.id);
      if (!v || v.owner !== p.id || u.epoch !== v.epoch || !validPose(u.pose)
        || u.pose.mode !== v.pose.mode || u.pose.vehicle !== v.pose.vehicle) { valid = false; continue; }
      const pose = cleanPose(u.pose);
      // Identical resting bodies do not churn revisions (timestamps aren't state).
      const before = { ...v.pose, t: 0 }, after = { ...pose, t: 0 };
      v.updated = now;
      if (JSON.stringify(before) !== JSON.stringify(after)) { v.pose = pose; this.changed(v); }
      if (v.occupant) v.used = now;
    }
    return valid;
  }
  disconnectPlayer(p) {
    for (const v of this.entries.values()) if (v.owner === p.id) {
      v.pose = { ...v.pose, vE: 0, vN: 0, vH: 0 };
      this.changed(v);
    }
  }
  reconnectPlayer(p, now) {
    // Reload must not consume the last seconds of an empty car's lifetime.
    for (const v of this.entries.values()) if (v.owner === p.id) v.used = now;
  }
  expire(now) {
    for (const [id, v] of this.entries) {
      const holder = this.room.players.get(v.occupant);
      // Presence idle timers never revoke a connected driver's seat. Only a
      // genuinely departed holder (with a reconnect reservation) frees it.
      if (v.occupant && (!holder || (!holder.connection && now - holder.disconnectedAt >= VEHICLE_LIMITS.reconnectMs))) {
        v.occupant = null; v.used = now; this.changed(v);
      }
      const owner = this.room.players.get(v.owner);
      const reconnecting = owner && !owner.connection && now - owner.disconnectedAt < VEHICLE_LIMITS.reconnectMs;
      if (!v.occupant && !reconnecting && now - v.used >= VEHICLE_LIMITS.lifetime) this.entries.delete(id);
    }
    for (const [key, at] of this.contacts) if (now - at > 1000) this.contacts.delete(key);
  }
  resolveContacts(now) {
    const all = [...this.entries.values()];
    for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) {
      const a = all[i], b = all[j];
      if (a.owner === b.owner || distance(a.pose, b.pose) > 18) continue;
      if ((!this.room.players.get(a.owner)?.online && !this.room.players.get(b.owner)?.online)
        || (Math.hypot(a.pose.vE, a.pose.vN) > 0.2 && now - a.updated > 1500)
        || (Math.hypot(b.pose.vE, b.pose.vN) > 0.2 && now - b.updated > 1500)) continue;
      const key = `${a.id}|${b.id}`;
      if (now - (this.contacts.get(key) ?? -Infinity) < 250 || now - a.impulseAt < 250 || now - b.impulseAt < 250) continue;
      const impulse = contactImpulse(a, b);
      if (!impulse) continue;
      this.contacts.set(key, now);
      // Transfer an empty target to the hitter, even if the old owner is online
      // but paused or has streamed the ground away. Send the pre-impulse pose
      // first, so the new simulation restores it and applies exactly one impulse.
      for (const [v, other] of [[a, b], [b, a]]) if (!v.occupant && v.owner !== other.owner
        && this.room.players.get(other.owner)?.online && (other.occupant || !this.room.players.get(v.owner)?.online
          || Math.hypot(other.pose.vE, other.pose.vN) > Math.hypot(v.pose.vE, v.pose.vN) + .2)) {
        v.owner = other.owner; v.epoch++; this.changed(v);
        this.room.send(this.room.players.get(v.owner)?.connection, 'vehicles', { upsert: [publicVehicle(v)], remove: [] });
      }
      for (const [v, sign] of [[a, -1], [b, 1]]) {
        const owner = this.room.players.get(v.owner);
        const mass = TUNING[v.pose.mode].mass;
        v.impulseAt = now;
        v.pose = { ...v.pose, vE: v.pose.vE + sign * impulse.E / mass, vN: v.pose.vN + sign * impulse.N / mass };
        this.changed(v);
        if (owner?.connection) this.room.send(owner.connection, 'vehicle_impulse', {
          id: v.id, epoch: v.epoch, sequence: v.revision, E: sign * impulse.E, N: sign * impulse.N });
      }
    }
  }
  flush(now, contacts = true) {
    if (contacts) this.resolveContacts(now);
    const sources = this.sources();
    for (const c of this.room.connections) {
      if (!c.player) continue;
      const previous = c.vehicleVersions ?? new Map(), next = new Map(), upsert = [], patches = [];
      const sent = c.vehicleSent ?? new Map(), snapshots = new Map();
      for (const v of this.entries.values()) if (this.nearby(v, c.player)) {
        next.set(v.id, v.revision);
        const snapshot = publicVehicle(v), old = sent.get(v.id);
        snapshots.set(v.id, snapshot);
        if (previous.get(v.id) === v.revision) continue;
        if (!old || old.epoch !== v.epoch || old.occupant !== v.occupant) upsert.push(snapshot);
        else if (v.owner !== c.player.id) {
          const pose = Object.fromEntries(Object.entries(v.pose).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(old.pose[key])));
          patches.push({ id: v.id, revision: v.revision, pose });
        }
      }
      const remove = [...previous.keys()].filter(id => !next.has(id));
      const sourcesChanged = JSON.stringify(c.vehicleSources) !== JSON.stringify(sources);
      if (upsert.length || patches.length || remove.length || sourcesChanged) this.room.send(c, 'vehicles', {
        upsert, remove, ...(patches.length ? { patches } : {}), ...(sourcesChanged ? { sources } : {}) });
      c.vehicleVersions = next; c.vehicleSources = sources; c.vehicleSent = snapshots;
    }
  }
}
