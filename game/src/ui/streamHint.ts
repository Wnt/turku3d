/** Placement/streaming holds use a small in-game status, never the startup loader. */
export class StreamHint {
  readonly element = document.createElement('div');
  constructor(parent: HTMLElement) {
    this.element.id = 'stream-hint';
    this.element.setAttribute('role', 'status');
    Object.assign(this.element.style, { position: 'fixed', bottom: '100px', left: '16px', color: 'white', pointerEvents: 'none' });
    this.element.textContent = 'Preparing your position…';
    this.element.hidden = true;
    parent.append(this.element);
  }
  update(holding: boolean) { this.element.hidden = !holding; }
}
