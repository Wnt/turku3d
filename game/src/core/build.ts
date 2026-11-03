// The code this page runs. The dev server stamps the served index.html with the worktree HEAD (and whether tracked
// files differ from it) at the moment the page loads: Vite serves live files, so the server's own start commit
// (import.meta.env.VITE_COMMIT) can be hours older. The dev build notice records newer HEADs it sees afterwards.
export interface BuildStatus { loaded: string | null; dirty: boolean; served: string | null; latest: string | null; pending: number }

const state = { latest: null as string | null, pending: new Set<string>() };

function stamp() {
  const content = typeof document === 'undefined' ? null
    : document.querySelector?.('meta[name="turku3d-build"]')?.getAttribute('content') ?? null;
  const [commit, flag] = (content ?? '').split('+');
  return { commit: commit || null, dirty: flag === 'dirty' };
}

/** Called by the dev build notice for every HEAD the dev server reports after load. */
export function noteServedHead(commit: string) {
  const loaded = stamp().commit;
  state.latest = commit;
  if (loaded && commit !== loaded) state.pending.add(commit);
}

export function buildStatus(): BuildStatus {
  const { commit, dirty } = stamp();
  return { loaded: commit, dirty, served: import.meta.env.VITE_COMMIT ?? null, latest: state.latest, pending: state.pending.size };
}
