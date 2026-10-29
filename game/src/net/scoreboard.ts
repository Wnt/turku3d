import type { MenuHost, MenuPage } from '../ui/menu';
import { multiplayerUrl, scoresUrl } from './config';
import { earnedAmount, type Hour, type MultiplayerClient, type Score } from './client';

/** Top three plus self, with duplicate entries removed when self is already on the podium. */
export function compactScores(scores: readonly Score[], nick: string | null): Score[] {
  const podium = scores.slice(0, 3);
  const self = nick ? scores.find(s => s.nick === nick) : null;
  return self && !podium.includes(self) ? [...podium, self] : podium;
}

const row = (score: Pick<Score, 'rank' | 'nick' | 'earned' | 'deliveries'>) =>
  `#${score.rank} ${score.nick} · Earned €${earnedAmount(score.earned)} · ${score.deliveries} ${score.deliveries === 1 ? 'delivery' : 'deliveries'}`;

/** Historical money totals cannot be reconstructed into gross earnings. Keep their original rank. */
export function normalizeArchivedScore(score: Score | (Omit<Score, 'earned'> & { money: number })): Score {
  return { ...score, earned: earnedAmount('earned' in score ? score.earned : score.money) };
}

/** HUD summary and full menu page. Finished hours are fetched only when the menu opens or browses. */
export class Scoreboard {
  readonly element = document.createElement('aside');
  private readonly banner = document.createElement('div');
  private readonly table = document.createElement('table');
  private client: MultiplayerClient | null = null;
  private hours: Hour[] = [];
  private index = -1; // -1 is the current live hour
  private scores: Score[] = [];
  private displayedHour: Hour | null = null;
  private host: MenuHost | null = null;
  private back: (() => void) | null = null;
  private status = '';
  private countdownStarted = 0;
  private countdownValue: number | null = null;
  private timer: ReturnType<typeof setInterval>;

  constructor(parent: HTMLElement) {
    this.element.id = 'mp-scoreboard';
    this.element.hidden = true;
    this.banner.className = 'mp-countdown';
    this.table.setAttribute('aria-label', 'Hourly leaderboard');
    this.element.append(this.banner, this.table);
    parent.append(this.element);
    this.timer = setInterval(() => this.renderCountdown(), 250);
  }

  setClient(client: MultiplayerClient | null) {
    this.client?.removeEventListener('change', this.onChange);
    this.client = client;
    client?.addEventListener('change', this.onChange);
    this.onChange();
  }

  private readonly onChange = () => {
    const state = this.client?.state;
    this.element.hidden = !state?.connected && !state?.error;
    if (state?.error && !state.connected) {
      this.banner.hidden = false; this.banner.textContent = state.error; this.table.textContent = ''; return;
    }
    if (!state?.connected) return;
    if (state.countdown !== this.countdownValue) {
      this.countdownValue = state.countdown;
      this.countdownStarted = performance.now();
    }
    this.renderCountdown();
    this.table.replaceChildren();
    const head = this.table.createTHead().insertRow();
    for (const title of ['#', 'Name', 'Earned', 'Deliveries']) {
      const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = title; head.append(cell);
    }
    const body = this.table.createTBody();
    const visible = compactScores(state.scores, state.you?.nick ?? null);
    for (const score of visible) {
      const tr = body.insertRow();
      tr.classList.toggle('mp-self', score.nick === state.you?.nick);
      tr.classList.toggle('mp-offline', !score.online);
      if (score.nick === state.you?.nick) tr.setAttribute('aria-current', 'true');
      if (state.scores.indexOf(score) > 2) tr.classList.add('mp-separated');
      for (const value of [score.rank, score.nick, `€${earnedAmount(score.earned)}`, score.deliveries]) {
        tr.insertCell().textContent = String(value);
      }
    }
    if (this.host && this.index === -1) {
      this.displayedHour = state.hour;
      this.scores = state.scores;
      this.show();
    }
  };

  private renderCountdown() {
    if (this.client?.state.error && !this.client.state.connected) return;
    const seconds = this.countdownValue;
    this.banner.hidden = seconds === null;
    this.banner.textContent = seconds === null ? ''
      : `Next hour in ${Math.max(0, Math.ceil(seconds - (performance.now() - this.countdownStarted) / 1000))} s`;
  }

  async open(host: MenuHost, back: () => void) {
    this.host = host;
    this.back = back;
    this.index = -1;
    this.status = 'Loading scores…';
    this.show();
    try {
      const base = this.client?.httpUrl ?? scoresUrl(multiplayerUrl());
      const [indexResponse, liveResponse] = await Promise.all([
        fetch(`${base}/scores/index.json`), fetch(`${base}/live.json`),
      ]);
      if (!indexResponse.ok || !liveResponse.ok) throw new Error(`HTTP ${indexResponse.status}/${liveResponse.status}`);
      this.hours = await indexResponse.json() as Hour[];
      const live = await liveResponse.json() as { hour: Hour; scores: Score[] };
      this.displayedHour = this.client?.state.hour ?? live.hour;
      this.scores = this.client?.state.connected ? this.client.state.scores : live.scores;
      this.status = '';
    } catch (error) { this.status = `Scores unavailable: ${String(error)}`; }
    this.show();
  }

  private async browse(next: number) {
    this.index = next;
    this.status = 'Loading scores…'; this.show();
    if (next === -1) { await this.open(this.host!, this.back!); return; }
    const hour = this.hours[next];
    try {
      const base = this.client?.httpUrl ?? scoresUrl(multiplayerUrl());
      const response = await fetch(`${base}/scores/${encodeURIComponent(hour.id)}.json`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as { hour: Hour; players: Score[] };
      this.displayedHour = data.hour; this.scores = data.players.map(normalizeArchivedScore); this.status = '';
    } catch (error) { this.status = `Scores unavailable: ${String(error)}`; }
    this.show();
  }

  private show() {
    if (!this.host) return;
    const hour = this.displayedHour;
    const rows = this.scores.map((score, i) => ({ id: `rank-${i}`, label: row(score), detail: null,
      enabled: false, run: () => {} }));
    const page: MenuPage = { id: 'scoreboard', title: 'Hourly scoreboard',
      text: `${hour?.id ?? 'Current hour'} · Europe/Helsinki${this.status ? ` · ${this.status}` : ''}`,
      items: [
        ...rows,
        { id: 'older', label: '‹ Older hour', detail: null, enabled: this.index + 1 < this.hours.length,
          run: () => { void this.browse(this.index + 1); } },
        { id: 'newer', label: 'Newer hour ›', detail: null, enabled: this.index >= 0,
          run: () => { void this.browse(this.index - 1); } },
        { id: 'back', label: 'Back', detail: null, enabled: true, run: () => this.close() },
      ], back: () => this.close() };
    this.host.show(page);
  }

  private close() { const back = this.back; this.host = null; this.back = null; back?.(); }
  dispose() { clearInterval(this.timer); this.client?.removeEventListener('change', this.onChange); this.element.remove(); }
}
