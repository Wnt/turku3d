// Small before/after screenshots of an edit: the next rendered frame, copied from the WebGPU canvas inside the frame's
// own task (window.__render.afterFrame, before the browser presents it) into a 384 px JPEG. Kept with the session.
const WIDTH = 384;

export class FrameShots {
  private waiting: ((blob: Blob | null) => void)[] = [];
  private previous: (() => void) | undefined;
  private installed = false;
  private canvas: HTMLCanvasElement | null = null;

  constructor(private source: () => HTMLCanvasElement | null) {}

  /** The next frame as a JPEG (null when the canvas cannot be read). */
  capture(): Promise<Blob | null> {
    return new Promise(resolve => {
      this.waiting.push(resolve); this.install();
      // A recorder that restored an older afterFrame dropped this hook: give up on this shot and reinstall next time.
      setTimeout(() => {
        const i = this.waiting.indexOf(resolve);
        if (i < 0) return;
        this.waiting.splice(i, 1); this.installed = false; resolve(null);
      }, 1500);
    });
  }

  private hook = () => { this.previous?.(); this.take(); };

  /** Installed once on the first capture and then idle between captures (another recorder may wrap it later). */
  private install() {
    const render = window.__render;
    if (this.installed || !render) return;
    this.installed = true;
    this.previous = render.afterFrame;
    render.afterFrame = this.hook;
  }

  private take() {
    if (!this.waiting.length) return;
    const waiting = this.waiting.splice(0);
    const finish = (blob: Blob | null) => { for (const resolve of waiting) resolve(blob); };
    const source = this.source();
    if (!source || !source.width) { finish(null); return; }
    const canvas = this.canvas ??= document.createElement('canvas');
    canvas.width = WIDTH; canvas.height = Math.max(1, Math.round(WIDTH * source.height / source.width));
    try {
      canvas.getContext('2d')!.drawImage(source, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(finish, 'image/jpeg', 0.82);
    } catch { finish(null); }
  }

  dispose() {
    for (const resolve of this.waiting.splice(0)) resolve(null);
    const render = window.__render;
    if (this.installed && render?.afterFrame === this.hook) render.afterFrame = this.previous;
    this.installed = false;
  }
}
