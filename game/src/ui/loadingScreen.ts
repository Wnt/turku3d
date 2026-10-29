import { loading, loadBytes } from '../core/loading';
import { worldLoad } from '../world/loadProgress';

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
  private readyMessage = 'Ready · choose Multiplayer to enter the city';
  private failure: string | null = null;
  private completedProgress = 0;

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
  ready(multiplayerReady = true) {
    this.menuReady = true;
    if (!multiplayerReady) this.readyMessage = 'World ready · Multiplayer unavailable';
    this.element.classList.add('menu-ready');
    this.draw();
  }

  /** The full-screen loader belongs only to startup, never to play or subsequent streaming. */
  enter() {
    if (this.finished) return;
    this.finished = true;
    this.element.hidden = true;
    loading.note('City entered');
    this.observer?.disconnect();
    loading.stop();
  }

  update(playing: boolean, draft?: string) {
    if (playing) this.enter();
    if (this.finished || this.failure) return;
    if (draft && draft !== this.draft) { this.draft = draft; loading.note(draft); }
  }

  fail(error: string) { if (this.finished) return; this.failure = error; this.draw(); this.element.classList.remove('menu-ready'); }

  private draw() {
    const world = worldLoad.snapshot();
    const stage = this.menuReady ? this.readyMessage : world.phase === 'compiling' ? 'Preparing graphics' : 'Preparing nearby streets';
    this.active.textContent = this.failure ? `Loading failed · ${this.failure}` : stage;
    // Stage-weighted, monotonic progress: discovering more work cannot move the bar backwards.
    // Counts below remain exact; these weights describe stages, not an ETA.
    const total = world.visual.total + world.collision.total;
    const next = this.menuReady ? 1 : world.phase === 'compiling'
      ? 0.75 + 0.24 * world.compile.done / Math.max(1, world.compile.total)
      : total ? 0.75 * (world.visual.loaded + world.collision.loaded) / total : 0;
    this.completedProgress = Math.max(this.completedProgress, next);
    this.progress.max = 1;
    if (total || this.menuReady) this.progress.value = this.completedProgress;
    else this.progress.removeAttribute('value');
    this.count.textContent = total
      ? `${world.visual.loaded}/${world.visual.total} models · ${world.collision.loaded}/${world.collision.total} collision cells · ${world.textures.loaded} GPU textures · ${world.compile.done}/${world.compile.total} warmup views · ${loadBytes(world.bytesLoaded)}`
      : `${loading.completed} tasks complete · ${loading.active[0] ?? 'Starting the engine'}`;
    this.log.textContent = loading.log.slice(-9).join('\n');
  }
}
