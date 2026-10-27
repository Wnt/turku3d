import { lineText } from '../../gameplay/content/dialogue';
import { LinePool } from '../../gameplay/poles/fine';
import type { VoicePlayer } from '../../audio/voice';

const lines = Array.from({ length: 12 }, (_, i) => lineText(`pole_fine_${String(i + 1).padStart(2, '0')}`));

/** Nonblocking police notice; the CSS swaps mouth frames at 6 Hz. */
export class PoleNotice {
  private pool = new LinePool(lines.length);
  private element = document.createElement('aside');
  private text = document.createElement('span');
  private timer: ReturnType<typeof setTimeout> | null = null;
  private talkTimer: ReturnType<typeof setInterval> | null = null;
  constructor(private readonly voice: VoicePlayer, parent: HTMLElement) {
    this.element.className = 'pole-notice';
    this.element.setAttribute('role', 'status');
    const portrait = document.createElement('div');
    portrait.className = 'pole-notice-portrait';
    const a = document.createElement('img'), b = document.createElement('img');
    a.src = `${import.meta.env.BASE_URL}assets/ui/officer-closed.svg`;
    b.src = `${import.meta.env.BASE_URL}assets/ui/officer-open.svg`;
    a.alt = 'Police officer'; b.alt = '';
    portrait.append(a, b);
    this.element.append(portrait, this.text);
    parent.append(this.element);
  }
  show() {
    const line = lines[this.pool.next()];
    this.text.textContent = line;
    this.voice.show(line, true);
    this.element.classList.remove('visible');
    void this.element.offsetWidth;
    this.element.classList.add('visible');
    if (this.timer) clearTimeout(this.timer);
    if (this.talkTimer) clearInterval(this.talkTimer);
    const clipId = `pole_fine_${String(lines.indexOf(line) + 1).padStart(2, '0')}`;
    this.talkTimer = setInterval(() => {
      const audio = this.voice.snapshot();
      this.element.classList.toggle('talking', audio.playing === clipId || audio.pending === clipId);
    }, 100);
    this.timer = setTimeout(() => {
      this.element.classList.remove('visible', 'talking');
      if (this.talkTimer) clearInterval(this.talkTimer);
      this.talkTimer = null;
    }, 4000);
  }
  dispose() { if (this.timer) clearTimeout(this.timer); if (this.talkTimer) clearInterval(this.talkTimer); this.element.remove(); }
}
