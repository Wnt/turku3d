import type { Score } from './client';
export interface TimedRound { id: string; minutes: number; phase: 'countdown' | 'running' | 'finished'; start: number; end: number; initiator: string }
export interface RoundResult extends TimedRound { finished: number; reason: 'time' | 'restarted'; scores: Score[] }
export interface RoundState {
  epoch: string | null;
  active: TimedRound | null;
  proposal: { id: string; initiator: string; nick: string; votes: string[]; needed: number; connected: number } | null;
  history: RoundResult[];
}
export const emptyRounds = (): RoundState => ({ epoch: null, active: null, proposal: null, history: [] });
export function roundTime(end: number, now: number): string {
  const seconds = Math.max(0, Math.ceil((end - now) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
