// Localhost game build badge (Vite dev server only; tools/dev/localhost/README.md): main's commit that the open tab
// runs and the integrator's verdict for it, `main <commit> · unverified` until its integration row is in, then
// `verified`, or `tests red: <stage>` when it was published with failing advisory checks (Chrome, audits); a build
// failure shows `verification failed: <stage>` and the player decides whether to stay. The status comes from
// GET /__localhost/status and the dev server's `turku3d:status` event; without the service it stays hidden.

export interface BuildStatus {
  service: boolean;
  commit?: string;
  verification?: { state: 'verified' | 'tests-red' | 'unverified' | 'failed'; stage?: string; detail?: string };
  /** The tile release the dev server streams: its store key and how it was produced. */
  tiles?: { key: string | null; source: string; building?: string[] | null };
  /** The coarse tile service the localhost service supervises: the commit it runs and its state (`running`,
   *  `starting`, `draining`, `restarting`, `exited`, `port in use`, `off`; tools/dev/localhost/coarse.mjs). */
  coarse?: { commit: string | null; state: string };
}

/** The badge line for a status, or null when no localhost service runs. */
export function badgeText(status: BuildStatus): string | null {
  if (!status.service || !status.commit) return null;
  const head = `main ${status.commit.slice(0, 7)}`;
  const v = status.verification;
  const line = v?.state === 'verified' ? `${head} · verified`
    : v?.state === 'tests-red' ? `${head} · tests red: ${v.stage ?? 'unknown stage'}`
    : v?.state === 'failed' ? `${head} · verification failed: ${v.stage ?? 'unknown stage'}`
      : `${head} · unverified${v?.detail ? ` (${v.detail})` : ''}`;
  const building = status.tiles?.building?.length ? ` · building tiles ${status.tiles.building.join(' ')}` : '';
  return line + building;
}

const COLORS = { verified: '#9be29b', 'tests-red': '#ffb86b', unverified: '#ffd98a', failed: '#ff9a8a' } as const;

export class BuildBadge {
  readonly element = document.createElement('div');

  constructor(parent: HTMLElement) {
    this.element.id = 'build-badge';
    this.element.setAttribute('role', 'status');
    Object.assign(this.element.style, { position: 'fixed', top: '8px', right: '8px', zIndex: '11', padding: '3px 10px',
      borderRadius: '4px', background: 'rgba(20,24,32,0.78)', font: '12px/1.4 ui-monospace, Menlo, monospace',
      pointerEvents: 'none', display: 'none' });
    parent.append(this.element);
  }

  set(status: BuildStatus) {
    const text = badgeText(status);
    this.element.style.display = text ? '' : 'none';
    this.element.textContent = text ?? '';
    this.element.dataset.state = status.verification?.state ?? 'unverified';
    this.element.style.color = COLORS[status.verification?.state ?? 'unverified'];
  }

  /** Reports a loaded page, an applied hot swap or a swapped tile release to the service's event log
   *  (landing-to-visible measurements). */
  applied(type: 'loaded' | 'hot-swap' | 'tiles') {
    void fetch('/__localhost/event', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type }) }).catch(() => {});
  }
}
