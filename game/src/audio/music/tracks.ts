import type { MusicState } from './index';

/** A streamed music track (public/assets/audio/music/<id>.ogg; sources and licences in ../../../public/assets/audio/LICENSES.md). */
export interface Track { id: string; name: string; style: string; bpm: number; seconds: number }

/** Six instrumental tracks generated with ACE-Step 1.5, as chosen on the soundboard of 2026-11-10. */
export const TRACKS: Track[] = [
  { id: 'menu', name: 'Harbour Glass', style: 'piano and strings, calm', bpm: 70, seconds: 113.8 },
  { id: 'synthwave', name: 'Aura Neon', style: 'night-drive synthwave', bpm: 108, seconds: 118.4 },
  { id: 'lofi', name: 'Market Sun', style: 'lo-fi hip hop', bpm: 80, seconds: 118.4 },
  { id: 'funk', name: 'Ring Road', style: '1970s cop-show funk', bpm: 112, seconds: 113.4 },
  { id: 'dubtechno', name: 'River Dub', style: 'dub techno', bpm: 120, seconds: 116.9 },
  { id: 'dnb', name: 'Last Slice', style: 'liquid drum and bass', bpm: 172, seconds: 115.9 },
];

/** Which tracks play in which state, in order. The menu theme loops. Free play rotates through four tracks, a new one
 *  when a track ends, after each delivery, and on N. A delivery run gets the chase track, then the funk. */
export const PLAYLISTS: Record<MusicState, readonly string[]> = {
  menu: ['menu'],
  explore: ['synthwave', 'lofi', 'funk', 'dubtechno'],
  mission: ['dnb', 'funk'],
};

export const trackOf = (id: string) => TRACKS.find(t => t.id === id)!;
