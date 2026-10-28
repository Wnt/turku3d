import { lineText } from '../content/dialogue';

/** The dispatcher repeats the delivery address this many seconds after the order announcement. */
export const CARRY_HINT_DELAY = 30;

/** Seconds a dialogue line stays on the message line: long enough to read it or hear it spoken. */
export function lineSeconds(text: string): number {
  return Math.min(7, Math.max(2.5, 1.2 + text.length * 0.06));
}

export interface Said { id: string; text?: string }
export const saidText = (said: Said) => said.text ?? lineText(said.id);
