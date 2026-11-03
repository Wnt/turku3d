// Delivery orders (docs/design/story.md, "Deliveries"): the scripted opening in a seeded order, then draws from the
// published address pool without repeats until it is exhausted, on a distance ladder that grows with the orders
// served, with an occasional short hop and every third order a double (two pizzas, two addresses, one run). Long
// jobs go to curated landmarks, every third one a double with a second landmark a real drive away, below
// LIMITED_MIN_METRES in all; runs of that length or more are the pizzeria's LIMITED TIME offers. Pure and seeded:
// the same seed, order number and delivered set always give the same order.
import { LIMITED_MIN_METRES, LONG_MAX_METRES } from '../economy';
import type { PoolAddress } from '../content/addresses';
import { DELIVERIES, type Delivery } from '../content/deliveries';

/** Route metres the ladder aims at for the first pool order, the step per order and the cap. */
export const LADDER_START_M = 200;
export const LADDER_STEP_M = 40;
export const LADDER_MAX_M = 900;
/** The ladder takes addresses between this share of the target and the target. */
const LADDER_BAND = 0.55;
/** Chance that an order is a short hop from the nearest quarter of what is left. */
const SHORT_CHANCE = 0.2;
/** Every DOUBLE_EVERY-th pool order and long job (1 in 3) carries two pizzas. */
export const DOUBLE_EVERY = 3;
/** The second address of a double is the nearest unused one to the first, within this straight distance ... */
const DOUBLE_MAX_GAP_M = 250;
/** ... but not the same door. */
const DOUBLE_MIN_GAP_M = 15;
/** The second landmark of a long double is a real drive from the first: this many route metres, never the next door
 *  and never a second cross-town run. */
export const LONG_LEG_MIN_M = 600;
export const LONG_LEG_MAX_M = 2500;

/** Deterministic 32-bit PRNG (mulberry32) returning [0, 1). */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The random stream of pool order `n` in a game with `seed`. */
const orderRandom = (seed: number, n: number) => rng((seed ^ Math.imul(n + 1, 0x9e3779b1)) >>> 0);

/** The scripted opening in this game's play order: a seeded permutation of DELIVERIES (Fisher-Yates). */
export function openingOrder(seed: number): Delivery[] {
  const random = rng(Math.imul(seed ^ 0x5bd1e995, 0x2c1b3c6d) >>> 0);
  const order = [...DELIVERIES];
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

export const ladderMetres = (n: number) => Math.min(LADDER_MAX_M, LADDER_START_M + LADDER_STEP_M * n);
export const isDouble = (n: number) => n % DOUBLE_EVERY === DOUBLE_EVERY - 1;

interface OrderDraw {
  /** Pool address ids in delivery order (one, or two for a double). */
  stops: string[];
  /** Every address had been used: the delivered set starts over (the result already ignores it). */
  exhausted: boolean;
}

/**
 * Draws pool order `n` (0 = the first after the scripted opening). `delivered` holds the ids already served;
 * `avoid` ids are left out on a first pass (the scripted opening's addresses) and the last order's stops never
 * repeat right after a reset. Null when the pool is empty.
 */
export function drawOrder(pool: readonly PoolAddress[], n: number, seed: number, delivered: ReadonlySet<string>,
  avoid: ReadonlySet<string> = new Set(), last: readonly string[] = []): OrderDraw | null {
  if (pool.length === 0) return null;
  let exhausted = false;
  let left = pool.filter(a => !delivered.has(a.id) && !avoid.has(a.id));
  if (left.length === 0) {
    exhausted = true;
    left = pool.filter(a => !last.includes(a.id));
    if (left.length === 0) left = [...pool];
  }
  const random = orderRandom(seed, n);
  const byDistance = [...left].sort((a, b) => a.foot - b.foot || (a.id < b.id ? -1 : 1));
  let choices: PoolAddress[];
  if (random() < SHORT_CHANCE) {
    choices = byDistance.slice(0, Math.max(1, Math.ceil(byDistance.length / 4)));
  } else {
    const target = ladderMetres(n);
    choices = byDistance.filter(a => a.foot <= target && a.foot >= target * LADDER_BAND);
    if (choices.length === 0) {
      // Nothing left in the band: the three addresses closest to the target.
      choices = [...byDistance].sort((a, b) => Math.abs(a.foot - target) - Math.abs(b.foot - target)).slice(0, 3);
    }
  }
  const first = choices[Math.floor(random() * choices.length)];
  const stops = [first.id];
  if (isDouble(n)) {
    const gap = (a: PoolAddress) => Math.hypot(a.point.x - first.point.x, a.point.z - first.point.z);
    const second = byDistance.filter(a => a !== first && gap(a) >= DOUBLE_MIN_GAP_M && gap(a) <= DOUBLE_MAX_GAP_M)
      .sort((a, b) => gap(a) - gap(b))[0];
    if (second) stops.push(second.id);
  }
  return { stops, exhausted };
}

/** Straight metres between two drop-offs: the leg measure when no street router is given. */
const straightLeg = (a: PoolAddress, b: PoolAddress) => Math.hypot(a.point.x - b.point.x, a.point.z - b.point.z);
type Leg = (from: PoolAddress, to: PoolAddress) => number;

/** Curated landmarks with a car route from the pizzeria, in id order. */
const drivableLandmarks = (pool: readonly PoolAddress[]) => pool
  .filter(a => a.landmark && a.car !== null && a.car > 60 && a.car <= LONG_MAX_METRES)
  .sort((a, b) => a.id.localeCompare(b.id));
const withinReach = (leg: number) => leg >= LONG_LEG_MIN_M && leg <= LONG_LEG_MAX_M;

/**
 * Draws long job `n` (counted by finished long jobs): one curated landmark, or for every third job two, the second an
 * unused landmark whose `leg` (route metres from the first drop-off) is LONG_LEG_MIN_M–LONG_LEG_MAX_M. The whole car
 * route stays below LIMITED_MIN_METRES (longer runs are the LIMITED TIME job): a double that would reach it takes a
 * closer second landmark or stays single. Long jobs never fall back to a nearby or undrivable address. Null when no
 * landmark qualifies.
 */
export function drawLongOrder(pool: readonly PoolAddress[], n: number, seed: number, delivered: ReadonlySet<string>,
  leg: Leg = straightLeg): PoolAddress[] | null {
  const eligible = drivableLandmarks(pool).filter(a => a.car! < LIMITED_MIN_METRES);
  const unused = eligible.filter(a => !delivered.has(a.id));
  const choices = unused.length ? unused : eligible;
  if (!choices.length) return null;
  const random = orderRandom(seed, n);
  const first = choices[Math.floor(random() * choices.length)];
  if (!isDouble(n)) return [first];
  const reach = (list: PoolAddress[]) => list.filter(a => a !== first && withinReach(leg(first, a))
    && first.car! + leg(first, a) < LIMITED_MIN_METRES);
  const unusedSeconds = reach(unused);
  const seconds = unusedSeconds.length ? unusedSeconds : reach(eligible);
  return seconds.length ? [first, seconds[Math.floor(random() * seconds.length)]] : [first];
}

/**
 * Draws LIMITED TIME offer `n` (counted by offers made): a run whose whole car route is LIMITED_MIN_METRES or more.
 * Every third offer is a double when some landmark pair reaches it (the second LONG_LEG_MIN_M–LONG_LEG_MAX_M from the
 * first); otherwise, and for the other offers, one landmark that far from the pizzeria. Landmarks in `used` (served,
 * or in the order being carried) are left out while others qualify. Null when nothing qualifies.
 */
export function drawLimitedOrder(pool: readonly PoolAddress[], n: number, seed: number, used: ReadonlySet<string>,
  leg: Leg = straightLeg): PoolAddress[] | null {
  const eligible = drivableLandmarks(pool);
  const random = rng((seed ^ Math.imul(n + 1, 0x85ebca6b) ^ 0x1b873593) >>> 0);
  const prefer = (list: PoolAddress[]) => {
    const unused = list.filter(a => !used.has(a.id));
    return unused.length ? unused : list;
  };
  if (isDouble(n)) {
    // Firsts in a seeded order; the first one with a far enough partner makes the double.
    const firsts = prefer(eligible).map(a => ({ a, key: random() })).sort((x, y) => x.key - y.key).map(x => x.a);
    for (const first of firsts) {
      const seconds = prefer(eligible.filter(a => a !== first && withinReach(leg(first, a))
        && first.car! + leg(first, a) >= LIMITED_MIN_METRES));
      if (seconds.length) return [first, seconds[Math.floor(random() * seconds.length)]];
    }
  }
  const singles = prefer(eligible.filter(a => a.car! >= LIMITED_MIN_METRES));
  return singles.length ? [singles[Math.floor(random() * singles.length)]] : null;
}
