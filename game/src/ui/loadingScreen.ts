import { loading, loadBytes } from '../core/loading';

/** Static HTML is visible before the entry bundle arrives. No fetch interception or resource duplication. */
export class LoadingScreen {
  private element = document.getElementById('loading-screen')!;
  private active = this.element.querySelector<HTMLElement>('.loading-active')!;
  private log = this.element.querySelector<HTMLElement>('.loading-log')!;
  private progress = this.element.querySelector<HTMLProgressElement>('progress')!;
  private count = this.element.querySelector<HTMLElement>('.loading-count')!;
  private observer: PerformanceObserver | null = null;
  private frame = 0;
  private finished = false;
  private menuReady = false;
  private draft = '';
  private failure: string | null = null;

  constructor(qa: boolean) {
    if (qa) { this.element.hidden = true; loading.stop(); return; }
    loading.subscribe(() => {
      if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); });
    });
    // Resource Timing reports real encoded/decoded bytes and durations for glTF, textures, audio and bundles.
    if (typeof PerformanceObserver !== 'undefined') {
      this.observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries() as PerformanceResourceTiming[]) {
          if (!/^https?:/.test(entry.name)) continue;
          const url = new URL(entry.name);
          const bytes = entry.encodedBodySize || entry.decodedBodySize;
          loading.note(`${decodeURIComponent(url.pathname).split('/').slice(-2).join('/')} · ${bytes ? loadBytes(bytes) : 'size unavailable'} · ${Math.round(entry.duration)} ms${entry.transferSize === 0 && bytes ? ' · cached' : ''}`);
        }
      });
      this.observer.observe({ type: 'resource', buffered: true });
    }
    this.draw();
  }

  /** Ready for menu input: keep the technical log in a compact dock while choosing Multiplayer. */
  ready() { this.menuReady = true; this.element.classList.add('menu-ready'); }

  update(playing: boolean, canMove: boolean, visualsReady: boolean, draft?: string) {
    if (this.finished || this.failure) return;
    if (draft && draft !== this.draft) { this.draft = draft; loading.note(draft); }
    if (this.menuReady) this.element.classList.toggle('menu-ready', !playing);
    if (playing && canMove && visualsReady) {
      this.finished = true;
      loading.note('City ready · movement enabled');
      this.draw();
      this.element.classList.add('leaving');
      setTimeout(() => { this.element.hidden = true; }, 500);
      this.observer?.disconnect();
      loading.stop();
    }
  }

  fail(error: string) { this.failure = error; this.draw(); this.element.classList.remove('menu-ready'); }

  private draw() {
    this.active.textContent = this.failure ? `Loading failed · ${this.failure}` : loading.active.join(' · ') || (this.menuReady ? 'Ready · choose Multiplayer to enter the city' : 'Preparing the city');
    this.progress.max = Math.max(1, loading.total);
    this.progress.value = loading.completed;
    this.count.textContent = `${loading.completed} / ${loading.total} tasks complete · more discovered as tiles stream`;
    this.log.textContent = loading.log.slice(-9).join('\n');
  }
}
