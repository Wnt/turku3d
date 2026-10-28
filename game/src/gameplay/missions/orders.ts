// Delivery orders after the scripted opening (docs/design/story.md, "Deliveries"): drawn from the published
// address pool without repeats until it is exhausted, on a distance ladder that grows with the orders served, with
// an occasional short hop and every fifth order a double (two pizzas, two addresses, one run). Pure and seeded:
// the same seed, order number and delivered set always give the same order.
import { LONG_MAX_METRES } from '../economy';
import type { PoolAddress } from '../content/addresses';

/** Route metres the ladder aims at for the first pool order, the step per order and the cap. */
export const LADDER_START_M = 200;
export const LADDER_STEP_M = 40;
export const LADDER_MAX_M = 900;
/** The ladder takes addresses between this share of the target and the target. */
const LADDER_BAND = 0.55;
/** Chance that an order is a short hop from the nearest quarter of what is left. */
const SHORT_CHANCE = 0.2;
/** Every DOUBLE_EVERY-th pool order (1 in 5) carries two pizzas. */
export const DOUBLE_EVERY = 5;
/** The second address of a double is the nearest unused one to the first, within this straight distance ... */
const DOUBLE_MAX_GAP_M = 250;
/** ... but not the same door. */
const DOUBLE_MIN_GAP_M = 15;

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

/** Long jobs never fall back to a nearby or undrivable address. */
export function drawLongOrder(pool: readonly PoolAddress[], n: number, seed: number,
  delivered: ReadonlySet<string>): PoolAddress | null {
  const eligible = pool.filter(a => a.landmark && a.car !== null && a.car > 60 && a.car <= LONG_MAX_METRES)
    .sort((a, b) => a.id.localeCompare(b.id));
  const unused = eligible.filter(a => !delivered.has(a.id));
  const choices = unused.length ? unused : eligible;
  return choices.length ? choices[Math.floor(orderRandom(seed, n)() * choices.length)] : null;
}
