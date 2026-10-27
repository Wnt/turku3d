import data from './phrases.json' with { type: 'json' };

export type PhraseKind = keyof typeof data;
export type PhraseCounts = Partial<Record<PhraseKind, number>>;
export const PHRASES: Record<PhraseKind, readonly string[]> = data;

/** A seeded permutation for each cycle. Counts live in the mission save, so reload cannot reset a pool. */
export function nextPhrase(kind: PhraseKind, counts: PhraseCounts, seed: number): string {
  const pool = PHRASES[kind];
  const count = counts[kind] ?? 0;
  const cycle = Math.floor(count / pool.length);
  const order = shuffled(pool.length, seed ^ hash(kind), cycle);
  if (cycle > 0 && order[0] === shuffled(pool.length, seed ^ hash(kind), cycle - 1).at(-1)) {
    [order[0], order[1]] = [order[1], order[0]];
  }
  counts[kind] = count + 1;
  return pool[order[count % pool.length]];
}

function shuffled(length: number, seed: number, cycle: number): number[] {
  const order = Array.from({ length }, (_, i) => i);
  let state = (seed ^ Math.imul(cycle + 1, 0x9e3779b9)) >>> 0;
  for (let i = order.length - 1; i > 0; i--) {
    state ^= state << 13; state ^= state >>> 17; state ^= state << 5;
    const j = (state >>> 0) % (i + 1);
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

function hash(value: string): number {
  let h = 2166136261;
  for (const char of value) h = Math.imul(h ^ char.charCodeAt(0), 16777619);
  return h;
}

/** The HUD sentence is also the exact sequence resolved by VoicePlayer from the voice manifest. */
export function addressSentence(phrase: string, first: string, second?: string): string {
  return `${phrase}${first}${second ? `. Then ${second}` : ''}.`;
}
