import type { MultiplayerClient } from './client';
import type { PauseMenu } from '../ui/menu';
import type { RoundResult } from './roundTypes';
import { roundTime } from './roundTypes';
import { roundPage, resultPage } from './roundMenu';

export class RoundUI {
  readonly banner = document.createElement('aside');
  readonly countdown = document.createElement('section');
  private minutes = 5;
  private wasBlocking = false;
  private signature = '';
  private timer: ReturnType<typeof setInterval>;
  constructor(private client: MultiplayerClient, private menu: PauseMenu,
    private back: () => void, private resume: () => void, private pause: () => void,
    private playing: () => boolean) {
    this.banner.id = 'round-banner'; this.banner.setAttribute('role', 'status');
    this.countdown.id = 'round-countdown'; this.countdown.setAttribute('role', 'status');
    this.countdown.hidden = true;
    document.body.append(this.banner, this.countdown);
    client.addEventListener('change', this.update);
    client.addEventListener('roundResult', this.result);
    this.timer = setInterval(this.update, 200);
    this.update();
  }
  get blocking() { return this.playing() && this.client.state.rounds.active?.phase === 'countdown'; }
  pauseState(paused: boolean, otherPause: boolean) {
    const blocked = this.blocking;
    const next = blocked ? true : this.wasBlocking ? otherPause : paused;
    this.wasBlocking = blocked;
    return next;
  }
  open = () => {
    this.menu.show(roundPage(this.client.state.rounds, this.client.state.you?.id, this.minutes,
      (action, minutes) => this.client.round(action, minutes), minutes => { this.minutes = minutes; this.open(); }, this.history, this.back));
  };
  history = () => {
    this.menu.show({ id: 'round-history', title: 'Past rounds', text: this.client.state.rounds.history.length ? null : 'No completed rounds yet.', back: this.open,
      items: [...this.client.state.rounds.history.map(r => ({ id: r.id,
        label: `${new Date(r.finished).toLocaleString()} · ${r.minutes} minutes`, detail: r.reason === 'restarted' ? 'Restarted' : 'Finished', enabled: true,
        run: () => this.menu.show(resultPage(r, this.history)) })),
      { id: 'back', label: 'Back', detail: null, enabled: true, run: this.open }] });
  };
  private result = (event: Event) => {
    const r = (event as CustomEvent<RoundResult>).detail;
    if (!this.playing() || r.reason === 'restarted') return;
    this.pause(); this.menu.show(resultPage(r, this.resume));
  };
  private update = () => {
    const state = this.client.state.rounds, p = state.proposal, active = state.active;
    const now = this.client.serverNow();
    this.banner.hidden = !this.client.state.connected || (!p && !active);
    this.banner.textContent = p ? `${p.nick} wants a new round · ${p.votes.length}/${p.needed} agree · Esc / Menu to vote`
      : active ? `Round · ${roundTime(active.end, now)} remaining` : '';
    this.countdown.hidden = !this.blocking;
    this.countdown.textContent = this.blocking && active ? `New round\n${Math.max(1, Math.ceil((active.start - now) / 1000))}\nGet ready!` : '';
    const signature = JSON.stringify([p, active?.id, active?.phase, this.client.state.connected]);
    if (signature !== this.signature) {
      this.signature = signature;
      if (this.menu.open && this.menu.panel.page?.id === 'rounds') this.open();
    }
  };
  dispose() {
    clearInterval(this.timer); this.client.removeEventListener('change', this.update);
    this.client.removeEventListener('roundResult', this.result); this.banner.remove(); this.countdown.remove();
  }
}
