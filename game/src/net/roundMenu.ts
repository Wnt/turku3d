import type { MenuPage } from '../ui/menu';
import type { RoundState, RoundResult } from './roundTypes';

/** All actions use ordinary menu rows, including gamepad and touch activation. */
export function roundPage(state: RoundState, self: string | undefined, minutes: number,
  act: (action: 'propose' | 'agree' | 'start', minutes?: number) => void,
  length: (minutes: number) => void, history: () => void, back: () => void): MenuPage {
  const p = state.proposal;
  const item = (id: string, label: string, enabled: boolean, run: () => void) => ({ id, label, enabled, run, detail: null });
  return { id: 'rounds', title: 'Timed multiplayer round',
    text: p ? `${p.nick} wants a new round · ${p.votes.length}/${p.needed} votes (${p.connected} connected)`
      : state.active ? `${state.active.minutes}-minute round in progress` : 'Vote to start a fresh game. At least half of connected players must agree.',
    back, items: [
      ...(!p ? [item('round-propose', state.active ? 'Restart timed round' : 'Start a new timed round', state.active?.phase !== 'countdown', () => act('propose'))] : [
        item('round-agree', p.votes.includes(self ?? '') ? 'You agreed' : 'Agree to new round', !p.votes.includes(self ?? ''), () => act('agree')),
        ...(p.initiator === self ? [
          item('round-shorter', '− 5 minutes', minutes > 5, () => length(minutes - 5)),
          item('round-longer', '+ 5 minutes', minutes < 180, () => length(minutes + 5)),
          item('round-start', `Start ${minutes}-minute round`, p.votes.length >= p.needed, () => act('start', minutes)),
        ] : []),
      ]),
      item('round-history', 'Past rounds', true, history), item('back', 'Back', true, back),
    ] };
}
export function resultPage(result: RoundResult, back: () => void): MenuPage {
  return { id: 'round-result', title: 'Round scoreboard',
    text: `${result.minutes} minutes · ${result.reason === 'restarted' ? 'Restarted' : 'Time is up'} · ${new Date(result.finished).toLocaleString()}`,
    back, items: [...result.scores.map(s => ({ id: `rank-${s.rank}`, label: `#${s.rank} ${s.nick}`,
      detail: `Earned €${s.earned} · ${s.deliveries} deliveries`, enabled: false, run() {} })),
    { id: 'dismiss', label: 'Continue playing', detail: null, enabled: true, run: back }] };
}
