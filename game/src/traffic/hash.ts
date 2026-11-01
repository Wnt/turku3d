// Deterministic 32-bit hashing for traffic: identical on every client and in Node (no Math.random, no locale).

/** FNV-1a over the parts' text, finished with a murmur avalanche. */
export function hash32(...parts: readonly (string | number)[]): number {
  let h = 0x811c9dc5;
  for (const part of parts) {
    const text = typeof part === 'number' ? numberText(part) : part;
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
    h = Math.imul(h ^ 0x7c, 0x01000193); // separator
  }
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35); h ^= h >>> 16;
  return h >>> 0;
}

/** Uniform in [0, 1). */
export const hash01 = (...parts: readonly (string | number)[]) => hash32(...parts) / 4294967296;

export const hex32 = (h: number) => (h >>> 0).toString(16).padStart(8, '0');

/** Integers print exactly; other numbers at 1 mm so float noise in derived values cannot change a hash. */
function numberText(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 1000));
}

/** Index into `weights` (which sum to about 1) for a uniform `u`. */
export function pick(u: number, weights: readonly number[]): number {
  let acc = 0;
  for (let i = 0; i < weights.length; i++) { acc += weights[i]; if (u < acc) return i; }
  return weights.length - 1;
}

/** Positive modulo. */
export const mod = (a: number, m: number) => ((a % m) + m) % m;
