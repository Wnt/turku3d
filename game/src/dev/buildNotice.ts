// Dev server only: Vite HMR is off (vite.config.ts), so merges never reload the page. Poll the served worktree's HEAD
// and show a small notice when it moves; the player reloads (F5) when it suits them.
import { noteServedHead } from '../core/build';

export function startBuildNotice(intervalMs = 20_000) {
  let first: string | null = null;
  const seen = new Set<string>();
  const el = document.createElement('div');
  el.id = 'build-notice';
  Object.assign(el.style, { position: 'fixed', right: '12px', bottom: '64px', zIndex: '20', padding: '4px 10px',
    font: '12px/1.4 system-ui, sans-serif', color: '#10161a', background: '#ffd98a', borderRadius: '6px',
    boxShadow: '0 1px 4px rgba(0,0,0,.35)', pointerEvents: 'none', display: 'none' });
  document.body.appendChild(el);
  const poll = async () => {
    try {
      const { commit } = await (await fetch('/__build', { cache: 'no-store' })).json() as { commit?: string };
      if (!commit) return;
      noteServedHead(commit);
      // The page's own build is the HEAD stamped into its HTML at load (core/build.ts), else the first poll.
      first ??= document.querySelector('meta[name="turku3d-build"]')?.getAttribute('content')?.split('+')[0] ?? commit;
      if (commit === first && !seen.size) return;
      if (commit !== first) seen.add(commit);
      if (seen.size) { el.textContent = `New build ready (${seen.size} update${seen.size > 1 ? 's' : ''}) \u00b7 F5 to load`; el.style.display = ''; }
    } catch { /* dev server restarting */ }
  };
  void poll();
  setInterval(() => { void poll(); }, intervalMs);
}
