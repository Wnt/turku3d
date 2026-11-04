// The dialog shown when an edit session closes: the session id and the paste-ready summary (summary.ts), each with
// one-press copy. Gamepad: ✕ [A] copy summary · △ [Y] copy id · ○ [B] close; keyboard Enter / F / Esc; mouse buttons.
// The clipboard API needs a secure context (localhost, city.lab over HTTPS) and may still be refused (no user
// activation from a gamepad press, permissions): then the text stays selected in its field for Ctrl/⌘+C.
import type { Input } from '../../input';

const CSS = `
#edit-session-dialog{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45);
  font:14px/1.4 system-ui,sans-serif;color:#eef}
#edit-session-dialog[hidden]{display:none}
#edit-session-dialog .esd-panel{background:#14161d;border:1px solid #3a4152;border-radius:8px;padding:16px 18px;width:min(860px,92vw);
  box-shadow:0 10px 40px rgba(0,0,0,.6)}
#edit-session-dialog h2{margin:0 0 10px;font-size:16px;font-weight:650}
#edit-session-dialog .esd-row{display:flex;gap:8px;align-items:center;margin-bottom:10px}
#edit-session-dialog input,#edit-session-dialog textarea{background:#0b0c10;color:#eef;border:1px solid #3a4152;border-radius:5px;
  font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;padding:6px 8px;box-sizing:border-box}
#edit-session-dialog input{flex:1;min-width:0}
#edit-session-dialog textarea{width:100%;height:min(260px,40vh);resize:vertical;white-space:pre;overflow:auto}
#edit-session-dialog button{background:#2a3142;color:#fff;border:1px solid #4b556c;border-radius:5px;padding:6px 12px;font:600 13px system-ui,sans-serif;
  cursor:pointer;white-space:nowrap}
#edit-session-dialog button:hover{background:#36405a}
#edit-session-dialog button b{color:#ffd76a;margin-left:6px}
#edit-session-dialog .esd-actions{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-top:10px}
#edit-session-dialog .esd-status{flex:1;color:#9fe6a0;min-height:1.4em}
#edit-session-dialog .esd-status.warn{color:#ffb36b}
`;

export type CopyResult = 'clipboard' | 'selected';

/** Copy `text`: the async clipboard first, then the selected field with execCommand, else leave it selected. */
export async function copyText(text: string, field: HTMLInputElement | HTMLTextAreaElement): Promise<CopyResult> {
  try {
    if (!window.isSecureContext || !navigator.clipboard?.writeText) throw new Error('no clipboard');
    await navigator.clipboard.writeText(text);
    return 'clipboard';
  } catch {
    field.focus(); field.select();
    try { if (document.execCommand('copy')) return 'clipboard'; } catch { /* refused: the selection stays */ }
    return 'selected';
  }
}

export class SessionDialog {
  readonly element = Object.assign(document.createElement('section'), { id: 'edit-session-dialog', hidden: true });
  private readonly idField = Object.assign(document.createElement('input'), { readOnly: true, spellcheck: false });
  private readonly textField = Object.assign(document.createElement('textarea'), { readOnly: true, spellcheck: false, wrap: 'off' });
  private readonly status = Object.assign(document.createElement('span'), { className: 'esd-status' });
  private readonly copyIdButton = this.button('Copy ID', () => void this.copy('id'));
  private readonly copySummaryButton = this.button('Copy summary', () => void this.copy('summary'));
  private readonly closeButton = this.button('Close', () => this.close());
  private readonly title = document.createElement('h2');
  private id = '';

  constructor(parent: HTMLElement = document.body) {
    if (!document.getElementById('edit-session-dialog-css')) document.head.append(Object.assign(document.createElement('style'), { id: 'edit-session-dialog-css', textContent: CSS }));
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-modal', 'true');
    const panel = Object.assign(document.createElement('div'), { className: 'esd-panel' });
    const idRow = Object.assign(document.createElement('div'), { className: 'esd-row' });
    idRow.append(this.idField, this.copyIdButton);
    const actions = Object.assign(document.createElement('div'), { className: 'esd-actions' });
    actions.append(this.status, this.copySummaryButton, this.closeButton);
    this.idField.setAttribute('aria-label', 'Session id');
    this.textField.setAttribute('aria-label', 'Session summary');
    panel.append(this.title, idRow, this.textField, actions);
    this.element.append(panel);
    // Typing in the page goes to the game; the dialog must not swallow F, Enter or Esc into its fields.
    this.element.addEventListener('keydown', e => { if (e.key !== 'c' || !(e.metaKey || e.ctrlKey)) e.preventDefault(); });
    parent.append(this.element);
  }

  get open() { return !this.element.hidden; }
  get text() { return this.textField.value; }

  show(id: string, summary: string, edits: number) {
    this.id = id;
    this.title.textContent = `Edit session closed · ${edits} edited object${edits === 1 ? '' : 's'} saved`;
    this.idField.value = id;
    this.textField.value = summary.trimEnd();
    this.setStatus('Paste the summary into a Claude session; the id finds the full rows.', false);
    this.element.hidden = false;
    // The mouse needs its pointer back to press the buttons.
    if (document.pointerLockElement) document.exitPointerLock?.();
  }

  close() { this.element.hidden = true; (document.activeElement as HTMLElement | null)?.blur?.(); }

  /** Per frame while open: the controller and keyboard actions of the game layer. Returns true while open. */
  handle(input: Input): boolean {
    if (!this.open) return false;
    this.label(this.copyIdButton, 'Copy ID', input.prompt('interact'));
    this.label(this.copySummaryButton, 'Copy summary', input.prompt('menuSelect'));
    this.label(this.closeButton, 'Close', input.prompt('menuBack'));
    if (input.consume('menuBack')) this.close();
    else if (input.consume('menuSelect')) void this.copy('summary');
    else if (input.consume('interact')) void this.copy('id');
    return this.open;
  }

  async copy(what: 'id' | 'summary'): Promise<CopyResult> {
    const field = what === 'id' ? this.idField : this.textField;
    const result = await copyText(what === 'id' ? this.id : this.textField.value + '\n', field);
    const thing = what === 'id' ? 'Session id' : 'Summary (with the session id)';
    if (result === 'clipboard') this.setStatus(`${thing} copied.`, false);
    else this.setStatus(`Clipboard refused: ${thing.toLowerCase()} is selected, press ${/Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'}+C.`, true);
    return result;
  }

  dispose() { this.element.remove(); }

  private setStatus(text: string, warn: boolean) { this.status.textContent = text; this.status.classList.toggle('warn', warn); }

  private label(button: HTMLButtonElement, text: string, glyph: string) {
    const html = `${text}<b>${glyph.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!)}</b>`;
    if (button.innerHTML !== html) button.innerHTML = html;
  }

  private button(text: string, act: () => void) {
    const b = Object.assign(document.createElement('button'), { type: 'button', textContent: text, tabIndex: -1 });
    // No focus: a focused button would also take the Enter that the game layer reads as "copy summary".
    b.addEventListener('mousedown', e => e.preventDefault());
    b.addEventListener('click', act);
    return b;
  }
}
