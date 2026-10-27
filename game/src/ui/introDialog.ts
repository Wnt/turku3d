import type { Input } from '../input';
import type { VoicePlayer } from '../audio/voice';
import { INTRO_LINE_IDS, lineText } from '../gameplay/content/dialogue';

/** Opening narration. The caller holds the world paused until this dialog resolves. */
export class IntroDialog {
  readonly element = document.createElement('section');
  private readonly line = document.createElement('p');
  private readonly count = document.createElement('p');
  private readonly hints = document.createElement('p');
  private finish: (() => void) | null = null;
  private index = 0;

  constructor(parent: HTMLElement, private readonly voice: VoicePlayer) {
    this.element.id = 'intro-dialog';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-modal', 'true');
    this.element.setAttribute('aria-labelledby', 'intro-title');
    this.element.hidden = true;
    this.element.innerHTML = '<div class="intro-panel"><p class="shop-speaker">Narrator</p><h2 id="intro-title">Turku, 2027</h2></div>';
    const panel = this.element.firstElementChild!;
    this.line.className = 'intro-line';
    this.count.className = 'intro-count';
    this.hints.className = 'shop-hints';
    const next = document.createElement('button');
    next.type = 'button'; next.textContent = 'Continue';
    next.addEventListener('click', () => this.advance());
    const skip = document.createElement('button');
    skip.type = 'button'; skip.textContent = 'Skip';
    skip.addEventListener('click', () => this.close());
    panel.append(this.line, this.count, next, skip, this.hints);
    parent.append(this.element);
  }

  get open() { return this.finish !== null; }

  show(): Promise<void> {
    if (this.open) this.close();
    this.index = 0;
    this.element.hidden = false;
    return new Promise(resolve => { this.finish = resolve; this.renderLine(); });
  }

  handle(input: Input) {
    if (!this.open) return;
    this.hints.textContent = `${input.prompt('menuSelect')} Continue · ${input.prompt('menuBack')} Skip`;
    if (input.consume('menuBack')) this.close();
    else if (input.consume('menuSelect')) this.advance();
  }

  private renderLine() {
    this.line.textContent = lineText(INTRO_LINE_IDS[this.index]);
    this.count.textContent = `${this.index + 1} / ${INTRO_LINE_IDS.length}`;
    this.voice.playDialogLine(this.line.textContent, () => this.advance());
  }

  private advance() {
    if (!this.open) return;
    this.voice.stopDialogLine();
    if (++this.index === INTRO_LINE_IDS.length) this.close();
    else this.renderLine();
  }

  private close() {
    if (!this.finish) return;
    this.voice.stopDialogLine();
    this.element.hidden = true;
    const finish = this.finish;
    this.finish = null;
    finish();
  }
}
