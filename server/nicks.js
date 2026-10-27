import { randomInt } from 'node:crypto';
export const BLOCKED_NICKS = new Set(['SS', 'SA', 'HH', 'KK', 'FU']);
export const NICKS = Array.from({ length: 676 }, (_, i) =>
  String.fromCharCode(65 + Math.floor(i / 26), 65 + i % 26)).filter(n => !BLOCKED_NICKS.has(n));

export function nickname(used, previous, random = randomInt) {
  const choices = NICKS.filter(n => !used.has(n) && n !== previous);
  if (!choices.length) return null;
  return choices[random(choices.length)];
}
