/** Visible fallback timing includes revalidation that produces identical bytes. */
export interface DraftFreshness {
  stale?: boolean;
  staleSince?: number;
  staleSeconds?: number;
  pass?: 'first' | 'full';
}

export function recordFreshness(e: DraftFreshness, fragment: { stale?: boolean; pass_kind?: 'first' | 'full' }) {
  const now = performance.now();
  if (fragment.stale) e.staleSince ??= now;
  else if (e.staleSince !== undefined) {
    e.staleSeconds = (now - e.staleSince) / 1000;
    e.staleSince = undefined;
  }
  e.stale = fragment.stale === true;
  e.pass = fragment.pass_kind;
}
