import type { MusicState } from './index';

/** A streamed music track (public/assets/audio/music/<id>.ogg; sources and licences in ../../../public/assets/audio/LICENSES.md). */
export interface Track { id: string; name: string; style: string; bpm: number; seconds: number }

/** Twenty instrumental tracks generated with ACE-Step 1.5: six chosen on the soundboard of 2026-11-10 and fourteen on
 *  the second board (sounds-music2) a day later. */
export const TRACKS: Track[] = [
  { id: 'menu', name: 'Harbour Glass', style: 'piano and strings, calm', bpm: 70, seconds: 113.8 },
  { id: 'synthwave', name: 'Aura Neon', style: 'night-drive synthwave', bpm: 108, seconds: 118.4 },
  { id: 'lofi', name: 'Market Sun', style: 'lo-fi hip hop', bpm: 80, seconds: 118.4 },
  { id: 'funk', name: 'Ring Road', style: '1970s cop-show funk', bpm: 112, seconds: 113.4 },
  { id: 'dubtechno', name: 'River Dub', style: 'dub techno', bpm: 120, seconds: 116.9 },
  { id: 'dnb', name: 'Last Slice', style: 'liquid drum and bass', bpm: 172, seconds: 115.9 },
  { id: 'italo', name: 'Night Ferry', style: 'Italo disco', bpm: 118, seconds: 119.4 },
  { id: 'boombap', name: 'Corner Kiosk', style: 'acid-jazz hip hop', bpm: 92, seconds: 116.6 },
  { id: 'ambient', name: 'Harbour Dawn', style: 'ambient', bpm: 60, seconds: 173.6 },
  { id: 'house', name: 'Sunrise Quay', style: 'deep house', bpm: 122, seconds: 146.9 },
  { id: 'downtempo', name: 'Evening Walk', style: 'downtempo electronica', bpm: 90, seconds: 146.6 },
  { id: 'disco', name: 'Mirror Ball Bridge', style: '1970s disco funk', bpm: 120, seconds: 116.4 },
  { id: 'triphop', name: 'Rain on the Aura', style: 'trip hop', bpm: 84, seconds: 150.0 },
  { id: 'motorway', name: 'Motorway Glow', style: 'driving synthwave', bpm: 150, seconds: 145.1 },
  { id: 'ambient-river', name: 'Night River', style: 'piano ambient', bpm: 60, seconds: 174.4 },
  { id: 'boombap2', name: 'Brass Block', style: 'neo-soul hip hop', bpm: 92, seconds: 114.4 },
  { id: 'ambient-sea', name: 'Open Sea', style: 'guitar ambient', bpm: 60, seconds: 176.6 },
  { id: 'italo2', name: 'Glitter Quay', style: 'Italo disco', bpm: 118, seconds: 119.1 },
  { id: 'darksynth', name: 'Blue Lights', style: 'dark synthwave', bpm: 126, seconds: 118.9 },
  { id: 'menu2', name: 'Sea Wind', style: 'piano, cello and synth pads', bpm: 66, seconds: 148.4 },
];

/** Which tracks play in which state, in order. The two menu themes alternate. Free play is a radio of seventeen tracks
 *  ordered so that calm and busy ones alternate: it starts at a random one each session and moves on when a track
 *  ends, after each delivery, and on N. A delivery run gets the chase track, then the dark synthwave, then the funk. */
export const PLAYLISTS: Record<MusicState, readonly string[]> = {
  menu: ['menu', 'menu2'],
  explore: ['synthwave', 'lofi', 'funk', 'dubtechno', 'italo', 'boombap', 'ambient', 'house', 'downtempo', 'disco', 'triphop',
    'motorway', 'ambient-river', 'boombap2', 'ambient-sea', 'italo2', 'darksynth'],
  mission: ['dnb', 'darksynth', 'funk'],
};

export const trackOf = (id: string) => TRACKS.find(t => t.id === id)!;
