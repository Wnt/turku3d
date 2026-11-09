import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, pose } from './helpers.js';
import { agree, chooseOwners, ROAD_LIMITS } from '../roads.js';

const last = (client, type) => client.messages.filter(m => m.type === type).at(-1);

test('each client hears the hashes every reporting player shares, for its own tiles only', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send({ type: 'roads', tiles: { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' } });
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' });
  // B shares t0_0_0 but holds another version of t0_1_0, and one tile A never reported.
  b.send({ type: 'roads', tiles: { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.cc', t0_2_0: '1.5.5.dd' } });
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa' });
  assert.deepEqual(last(b, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_2_0: '1.5.5.dd' });
  // When B leaves, A's tile is agreed again.
  f.room.disconnect(b.c);
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' });
  assert.equal(f.room.stats.rejected, 0);
});

test('unchanged agreement is not resent; malformed reports are rejected', () => {
  const f = fixture(), a = f.connect();
  a.send({ type: 'roads', tiles: { t0_0_0: 'x' } });
  a.send({ type: 'roads', tiles: { t0_0_0: 'x' } });
  assert.equal(a.messages.filter(m => m.type === 'roads').length, 1);
  for (const tiles of [null, [], { 'bad id': 'x' }, { t0_0_0: 'has space' },
    Object.fromEntries(Array.from({ length: ROAD_LIMITS.tiles + 1 }, (_, i) => [`t0_${i}_0`, 'x']))]) {
    a.send({ type: 'roads', tiles });
  }
  assert.equal(f.room.stats.rejected, 5);
});

test('agreement rule: a conflict removes the tile for everyone', () => {
  const reports = [new Map([['a', '1'], ['b', '2']]), new Map([['a', '1'], ['b', '3']]), new Map([['b', '2']])];
  assert.deepEqual([...agree(reports)], [['a', '1']]);
});

// Traffic authority: one holder of each shared tile runs its cars and the room relays them to the other holders.
const block = (cx, cz, r = 1, hash = 'h') => Object.fromEntries(Array.from({ length: (2 * r + 1) ** 2 },
  (_, i) => [`t0_${cx - r + i % (2 * r + 1)}_${cz - r + Math.floor(i / (2 * r + 1))}`, hash]));
const id = client => client.welcome.you.id;
const car = ['t0_0_0:7:3', '~7', '~7', 3, 12.5, 8, 1, 0, 0.25, 4];
/** Advances the room clock by `ms` in 1 s steps while every client sends a pose (the room takes a player silent for
 *  LIMITS.idleMs offline) and the owner of `tile` also an (empty) snapshot of it. */
let poseTime = 1;
function run(f, clients, ms, tile = 't0_0_0') {
  for (let t = 0; t < ms; t += 1000) {
    const owner = clients.find(x => last(x, 'roads')?.owners?.[tile] === id(x));
    owner?.send({ type: 'traffic', tick: 1000 + t / 100, tiles: { [tile]: [] } });
    for (const x of clients) x.send({ ...pose, t: poseTime++ });
    f.advance(1000);
  }
}

test('a shared tile gets one owner: settled before new, its whole neighbourhood before its edge, lowest id last', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  f.advance(25_000);
  a.send({ type: 'roads', tiles: block(0, 0) });
  // Alone, nobody owns anything: each client runs its own tiles as before.
  assert.deepEqual(last(a, 'roads').owners, {});
  // B's block is one tile east: t0_0_0 has its whole neighbourhood at A only, t0_1_0 at B only, the rest at neither.
  b.send({ type: 'roads', tiles: block(1, 0) });
  const owners = last(a, 'roads').owners;
  assert.deepEqual(Object.keys(owners).sort(), ['t0_0_-1', 't0_0_0', 't0_0_1', 't0_1_-1', 't0_1_0', 't0_1_1']);
  assert.equal(owners.t0_0_0, id(a));
  assert.equal(owners.t0_1_0, id(b));
  const low = [id(a), id(b)].sort()[0];
  for (const tile of ['t0_0_-1', 't0_0_1', 't0_1_-1', 't0_1_1']) assert.equal(owners[tile], low);
  // B hears the owners of its own tiles only; the tiles only it holds have none.
  assert.deepEqual(last(b, 'roads').owners, owners);
  // A new player is passed over for a tile nobody owned yet until it is settled.
  const c = f.connect();
  c.send({ type: 'roads', tiles: block(5, 5) });
  b.send({ type: 'roads', tiles: { ...block(1, 0), ...block(5, 5) } });
  assert.equal(last(c, 'roads').owners.t0_5_5, id(b));
  assert.equal(f.room.stats.rejected, 0);
});

test('an owner keeps its tile when another player arrives, and passes it on when it leaves', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  f.advance(25_000);
  a.send({ type: 'roads', tiles: block(0, 0) });
  b.send({ type: 'roads', tiles: block(0, 0) });
  const first = last(a, 'roads').owners.t0_0_0;
  assert.ok([id(a), id(b)].includes(first));
  const c = f.connect();
  c.send({ type: 'roads', tiles: block(0, 0) });
  run(f, [a, b, c], 25_000);
  assert.equal(last(c, 'roads').owners.t0_0_0, first);
  f.room.disconnect((first === id(a) ? a : b).c);
  const next = last(c, 'roads').owners.t0_0_0;
  assert.ok(next && next !== first);
});

test('the room relays an owner\'s cars to each other holder, only for the tiles that holder reported', () => {
  const f = fixture(), a = f.connect(), b = f.connect(), c = f.connect();
  f.advance(25_000);
  a.send({ type: 'roads', tiles: block(0, 0) });
  b.send({ type: 'roads', tiles: block(0, 0) });
  c.send({ type: 'roads', tiles: { t0_0_0: 'h' } });
  const owners = last(a, 'roads').owners, owner = [a, b, c].find(x => id(x) === owners.t0_1_1), other = [a, b].find(x => x !== owner);
  assert.ok(owner && owner !== c, 'the 3x3 holders own their shared block');
  owner.send({ type: 'traffic', tick: 900, tiles: { t0_0_0: [car], t0_1_1: [] } });
  assert.deepEqual(last(other, 'traffic'), { v: 2, type: 'traffic', from: id(owner), tick: 900, tiles: { t0_0_0: [car], t0_1_1: [] } });
  const toC = last(c, 'traffic');
  if (owners.t0_0_0 === id(owner)) assert.deepEqual(toC.tiles, { t0_0_0: [car] });
  assert.equal(owner.messages.filter(m => m.type === 'traffic').length, 0);
  // Tiles the sender does not own are dropped silently (a hand-over in flight); malformed snapshots are rejected.
  const before = other.messages.length;
  other.send({ type: 'traffic', tick: 901, tiles: { t0_1_1: [car] } });
  assert.equal(other.messages.length, before);
  assert.equal(f.room.stats.rejected, 0);
  for (const bad of [{ tick: -1, tiles: { t0_0_0: [] } }, { tick: 1.5, tiles: { t0_0_0: [] } }, { tick: 1, tiles: [] },
    { tick: 1, tiles: {} }, { tick: 1, tiles: { 'bad id': [] } }, { tick: 1, tiles: { t0_0_0: [car.concat([{}, 1])] } },
    { tick: 1, tiles: { t0_0_0: Array(ROAD_LIMITS.trafficCars + 1).fill(car) } }]) other.send({ type: 'traffic', ...bad });
  assert.equal(f.room.stats.rejected, 7);
});

test('an owner whose snapshots stop is passed over, and a candidate again after the mute', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  f.advance(25_000);
  a.send({ type: 'roads', tiles: block(0, 0) });
  b.send({ type: 'roads', tiles: block(0, 0) });
  const owner = last(a, 'roads').owners.t0_0_0, live = id(a) === owner ? a : b, quiet = live === a ? b : a;
  // The owner sends for a while, then stalls.
  for (let k = 0; k < 4; k++) { live.send({ type: 'traffic', tick: 1000 + k * 10, tiles: { t0_0_0: [] } }); f.advance(1000); }
  assert.equal(last(quiet, 'roads').owners.t0_0_0, owner);
  f.advance(ROAD_LIMITS.staleMs + 1000);
  assert.equal(last(quiet, 'roads').owners.t0_0_0, id(quiet));
  // The new owner keeps sending, so it keeps the tile after the mute ends (the old one is a candidate again).
  run(f, [a, b], ROAD_LIMITS.muteMs + 2000);
  assert.equal(live.c.player.trafficMutedUntil, undefined);
  assert.equal(last(live, 'roads').owners.t0_0_0, id(quiet));
  assert.equal(f.room.stats.rejected, 0);
});

test('owner rule: kept unless it lacks the neighbourhood a settled candidate has', () => {
  const roads = (tiles) => new Map(Object.entries(tiles));
  const agreed = new Map(Object.entries(block(0, 0, 2)));
  const edge = { id: 'a', roads: roads(block(1, 1)) }, inner = { id: 'b', roads: roads(block(0, 0)) };
  assert.equal(chooseOwners(agreed, [edge, inner]).get('t0_0_0'), 'b');
  assert.equal(chooseOwners(agreed, [edge, inner], new Map([['t0_0_0', 'a']])).get('t0_0_0'), 'b');
  assert.equal(chooseOwners(agreed, [edge, inner], new Map([['t0_0_0', 'a']]), { settled: p => p.id === 'a' }).get('t0_0_0'), 'a');
  assert.equal(chooseOwners(agreed, [edge, inner], new Map(), { candidate: p => p.id === 'a' }).get('t0_0_0'), 'a');
  assert.equal(chooseOwners(agreed, [edge, inner], new Map(), { candidate: () => false }).size, 0);
  assert.equal(chooseOwners(agreed, [inner]).size, 0);
});
