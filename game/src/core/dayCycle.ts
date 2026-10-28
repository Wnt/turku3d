/** UTC wall-clock phase shared by every client. 15 October 2027 is EEST (UTC+3). */
export const CYCLE_EPOCH = Date.UTC(2027, 9, 15, 5, 45);
export const DAY_MS = 30 * 60_000;
export const TRANSITION_MS = 10_000;
const START = 8 * 60 + 45, END = 17 * 60 + 30;
const LOCAL_MIDNIGHT = Date.UTC(2027, 9, 14, 21);
const dateAt = (minutes: number) => new Date(LOCAL_MIDNIGHT + minutes * 60_000);

/** The clock fast-forwards through night; the sky traverses daylight with a smooth evening-to-morning blend. */
export function dayCycle(utcMs: number) {
  const period = DAY_MS + TRANSITION_MS;
  const phase = ((utcMs - CYCLE_EPOCH) % period + period) % period;
  const transition = phase >= DAY_MS;
  const t = transition ? (phase - DAY_MS) / TRANSITION_MS : phase / DAY_MS;
  const ease = t * t * (3 - 2 * t);
  const minutes = transition ? (END + (1440 - END + START) * t) % 1440 : START + (END - START) * t;
  // Keep every sky sample within the daylight range: never render the unsupported night lighting.
  const lightMinutes = transition ? END + (START - END) * ease : minutes;
  // Fade the HDR solar disc during the rapid sweep so its bloom cannot flash across the player's view.
  const fade = transition ? Math.min(1, Math.min(t, 1 - t) / 0.2) : 0;
  const sunDisc = 1 - fade * fade * (3 - 2 * fade);
  return { time: dateAt(minutes), lightTime: dateAt(lightMinutes), sunDisc, transition, phase };
}
