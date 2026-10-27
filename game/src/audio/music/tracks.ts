export type Stem = 'pad' | 'bass' | 'drums' | 'lead' | 'percussion' | 'panic';
export const STEMS: Stem[] = ['pad', 'bass', 'drums', 'lead', 'percussion', 'panic'];
export interface Track {
  id: string; name: string; style: string; bpm: number; key: string; root: number;
  progression: number[]; wave: OscillatorType; liftStart?: number; tempoLift?: number; panicStem?: Stem;
}
/** All pitches and patterns are original, synthesized at playback. */
export const TRACKS: Track[] = [
  { id: 'aura-neon', name: 'Aura Neon', style: 'night synthwave', bpm: 108, key: 'A minor', root: 220, progression: [0, 5, 3, 7], wave: 'sawtooth', liftStart: 0.62, tempoLift: 0.2 },
  { id: 'market-sun', name: 'Market Sun', style: 'acoustic daytime', bpm: 102, key: 'C major', root: 261.63, progression: [0, 5, 2, 7], wave: 'triangle', liftStart: 0.7, tempoLift: 0.15 },
  { id: 'river-dub', name: 'River Dub', style: 'dub bass', bpm: 92, key: 'D minor', root: 293.66, progression: [0, 3, 7, 5], wave: 'sine', liftStart: 0.58, tempoLift: 0.24, panicStem: 'bass' },
  { id: 'ring-road', name: 'Ring Road', style: 'motorik rock', bpm: 120, key: 'E minor', root: 329.63, progression: [0, 5, 7, 3], wave: 'square', liftStart: 0.64, tempoLift: 0.18, panicStem: 'percussion' },
  { id: 'harbour-glass', name: 'Harbour Glass', style: 'minimal piano ambient', bpm: 84, key: 'G major', root: 196, progression: [0, 4, 7, 2], wave: 'triangle', liftStart: 0.72, tempoLift: 0.22 },
];
