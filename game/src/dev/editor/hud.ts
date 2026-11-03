// Editor HUD: centre crosshair, hovered/held object label, delete-hold bar, reason picker and the control-hint strip.
// Plain DOM over the canvas, pointer-events none; nothing renders while the mode is off.
const CSS = `
#object-editor{position:fixed;inset:0;pointer-events:none;z-index:40;font:13px/1.35 system-ui,sans-serif;color:#fff}
#object-editor[hidden]{display:none}
#object-editor .oe-cross{position:absolute;left:50%;top:50%;width:22px;height:22px;margin:-11px 0 0 -11px}
#object-editor .oe-cross::before,#object-editor .oe-cross::after{content:'';position:absolute;background:#fff;box-shadow:0 0 2px #000}
#object-editor .oe-cross::before{left:10px;top:0;width:2px;height:22px}
#object-editor .oe-cross::after{top:10px;left:0;height:2px;width:22px}
#object-editor.hover .oe-cross::before,#object-editor.hover .oe-cross::after{background:#5ef0ff}
#object-editor.holding .oe-cross::before,#object-editor.holding .oe-cross::after{background:#ffb347}
#object-editor .oe-label{position:absolute;left:50%;top:calc(50% + 20px);transform:translateX(-50%);background:rgba(0,0,0,.62);
  padding:3px 8px;border-radius:4px;white-space:nowrap;max-width:80vw;overflow:hidden;text-overflow:ellipsis}
#object-editor .oe-label:empty{display:none}
#object-editor .oe-bar{position:absolute;left:50%;top:calc(50% - 30px);width:120px;height:6px;margin-left:-60px;background:rgba(0,0,0,.5);border-radius:3px;overflow:hidden}
#object-editor .oe-bar i{display:block;height:100%;width:0;background:#ff5252}
#object-editor .oe-bar[hidden]{display:none}
#object-editor .oe-reasons{position:absolute;left:50%;top:calc(50% + 46px);transform:translateX(-50%);background:rgba(20,20,30,.82);
  padding:6px 10px;border-radius:6px;display:grid;grid-template-columns:auto auto;gap:2px 14px}
#object-editor .oe-reasons[hidden]{display:none}
#object-editor .oe-reasons b{color:#5ef0ff;font-weight:600}
#object-editor .oe-hints{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);background:rgba(0,0,0,.66);padding:5px 12px;
  border-radius:6px;white-space:nowrap;font-size:12px}
#object-editor .oe-hints b{color:#ffd76a;font-weight:600}
#object-editor .oe-status{position:absolute;right:12px;bottom:14px;background:rgba(0,0,0,.55);padding:3px 8px;border-radius:4px;font-size:11px;opacity:.9}
#object-editor .oe-counts{position:absolute;right:12px;top:12px;background:rgba(0,0,0,.6);padding:4px 10px;border-radius:5px;font-weight:600}
#object-editor .oe-counts.pending{background:rgba(200,90,20,.9)}
#object-editor .oe-toast{position:absolute;left:50%;top:22%;transform:translateX(-50%);background:rgba(0,0,0,.7);padding:5px 12px;border-radius:5px}
#object-editor .oe-toast:empty{display:none}
`;

export const REASON_LABELS = ['on road or path', 'wrong facing', "doesn't exist", 'misaligned with LiDAR'];

export interface HudView {
  mode: 'aim' | 'hold';
  label: string;
  hover: boolean;
  /** 0..1 progress of a delete hold, null when not held. */
  deleting: number | null;
  /** Seconds left of the reason window, 0 when closed; glyphs ↑ → ↓ ← in reason order. */
  reasonLeft: number;
  reasonGlyphs: string[];
  hints: [string, string][];
  status: string;
  /** Edits the dev server holds and rows still waiting (always shown in the mode). */
  counts: { saved: number; pending: number } | null;
}

export class EditorHud {
  readonly element = Object.assign(document.createElement('div'), { id: 'object-editor', hidden: true });
  private label = Object.assign(document.createElement('div'), { className: 'oe-label' });
  private bar = Object.assign(document.createElement('div'), { className: 'oe-bar', hidden: true });
  private fill = document.createElement('i');
  private reasons = Object.assign(document.createElement('div'), { className: 'oe-reasons', hidden: true });
  private hints = Object.assign(document.createElement('div'), { className: 'oe-hints' });
  private status = Object.assign(document.createElement('div'), { className: 'oe-status' });
  private toast = Object.assign(document.createElement('div'), { className: 'oe-toast' });
  private counts = Object.assign(document.createElement('div'), { className: 'oe-counts' });
  private toastUntil = 0;
  private last = '';

  constructor(parent: HTMLElement = document.body) {
    const style = document.createElement('style');
    style.textContent = CSS;
    this.bar.append(this.fill);
    this.element.append(style, Object.assign(document.createElement('div'), { className: 'oe-cross' }), this.label, this.bar,
      this.reasons, this.toast, this.hints, this.status, this.counts);
    parent.append(this.element);
  }

  show(on: boolean) { this.element.hidden = !on; }

  notify(text: string, now: number, ms = 1600) { this.toast.textContent = text; this.toastUntil = now + ms; }

  render(view: HudView, now: number) {
    if (now > this.toastUntil && this.toast.textContent) this.toast.textContent = '';
    this.element.classList.toggle('hover', view.hover && view.mode === 'aim');
    this.element.classList.toggle('holding', view.mode === 'hold');
    if (this.label.textContent !== view.label) this.label.textContent = view.label;
    const counts = view.counts ? `Edits saved ${view.counts.saved} · pending ${view.counts.pending}` : 'No session yet';
    if (this.counts.textContent !== counts) this.counts.textContent = counts;
    this.counts.classList.toggle('pending', !!view.counts?.pending);
    this.bar.hidden = view.deleting === null;
    if (view.deleting !== null) this.fill.style.width = `${Math.round(view.deleting * 100)}%`;
    const reasons = view.reasonLeft > 0 ? view.reasonGlyphs.map((g, i) => `${g}|${REASON_LABELS[i]}`).join(';') + `;${view.reasonLeft.toFixed(1)}` : '';
    this.reasons.hidden = !reasons;
    const key = `${view.hints.map(h => h.join(':')).join('|')}#${view.status}#${reasons}`;
    if (key === this.last) return;
    this.last = key;
    this.hints.replaceChildren(...view.hints.flatMap(([glyph, text], i) => {
      const b = document.createElement('b'); b.textContent = glyph;
      return [...(i ? [document.createTextNode('  ·  ')] : []), b, document.createTextNode(` ${text}`)];
    }));
    this.status.textContent = view.status;
    if (reasons) {
      this.reasons.replaceChildren(Object.assign(document.createElement('span'), { textContent: `Reason? (${view.reasonLeft.toFixed(1)} s, skip: ignore)` }),
        document.createElement('span'),
        ...view.reasonGlyphs.flatMap((g, i) => {
          const b = document.createElement('b'); b.textContent = g;
          const s = document.createElement('span'); s.append(b, ` ${REASON_LABELS[i]}`);
          return [s];
        }));
    }
  }

  dispose() { this.element.remove(); }
}
