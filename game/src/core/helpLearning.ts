/** A mode is learned after one minute of exposure or two separate uses of at least 75% of its prompts. */
export interface HelpProgress { elapsedMs: number; counts: Record<string, number> }
export function helpHidden(progress: HelpProgress, keys: readonly string[]): boolean {
  return progress.elapsedMs >= 60_000 || keys.length > 0 &&
    keys.filter(key => (progress.counts[key] ?? 0) >= 2).length >= Math.ceil(keys.length * 0.75);
}

/** One sample counts a logical prompt only once, even if several of its bindings were pressed together. */
export function recordHelp(progress: HelpProgress, pressed: readonly string[], elapsedMs: number): HelpProgress {
  const counts = { ...progress.counts };
  for (const key of new Set(pressed)) counts[key] = Math.min(2, (counts[key] ?? 0) + 1);
  return { counts, elapsedMs: Math.min(60_000, progress.elapsedMs + Math.max(0, elapsedMs)) };
}
