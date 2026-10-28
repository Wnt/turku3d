// Player-facing safe-reset lines (gameplay/recovery.ts): the lost prompt with its countdown, the "back on solid
// ground" line that fades after a reset, and the edge-of-the-built-area notice.
import { RECOVERY, type LostReason } from '../gameplay/recovery';

const LOST_TEXT: Record<LostReason, string> = {
  below: 'You left the map', outside: 'You left the map', airborne: 'You left the map', water: 'You fell into the river',
};

export class RecoveryHud {
  readonly element = document.createElement('div');
  private readonly line = document.createElement('div');
  private readonly edge = document.createElement('div');
  private fadeTimer = 0;

  constructor(parent: HTMLElement) {
    this.element.id = 'recovery';
    this.line.className = 'recovery-line';
    this.line.setAttribute('role', 'status');
    this.edge.className = 'recovery-edge';
    this.edge.textContent = 'Edge of the built area';
    this.line.hidden = this.edge.hidden = true;
    this.element.append(this.line, this.edge);
    parent.append(this.element);
  }

  /** The lost prompt; `button` is the respawn prompt for the active input source. */
  lost(reason: LostReason, seconds: number, button: string) {
    clearTimeout(this.fadeTimer);
    this.line.classList.remove('fading');
    this.line.hidden = false;
    this.line.textContent = `${LOST_TEXT[reason]}. Press ${button} to return to solid ground · ${Math.ceil(seconds)}`;
  }

  /** Hides the lost prompt once nothing is lost; the fading reset confirmation stays. */
  clearLost() {
    if (!this.line.hidden && !this.line.classList.contains('fading')) this.line.hidden = true;
  }

  /** After a reset: a short confirmation that fades out. */
  recovered() {
    this.line.hidden = false;
    this.line.textContent = 'Back on solid ground';
    this.line.classList.remove('fading');
    void this.line.offsetWidth; // restart the fade transition
    this.line.classList.add('fading');
    clearTimeout(this.fadeTimer);
    this.fadeTimer = window.setTimeout(() => { this.line.hidden = true; this.line.classList.remove('fading'); }, RECOVERY.fade * 1000);
  }

  /** Shows the edge notice while the player is within `RECOVERY.edgeWarning` metres of the area's edge. */
  setEdge(near: boolean) { if (this.edge.hidden === near) this.edge.hidden = !near; }

  snapshot() {
    return { line: this.line.hidden ? null : this.line.textContent, fading: this.line.classList.contains('fading'),
      edge: !this.edge.hidden };
  }
}
