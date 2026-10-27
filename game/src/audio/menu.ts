import type { PauseMenu, MenuPage } from '../ui/menu';
import type { GameAudio } from './index';

/** Pause-menu controls for the independent music bus. */
export function showAudioPage(menu: PauseMenu, audio: GameAudio, back: () => MenuPage) {
  const label = document.createElement('label'); label.textContent = 'Music volume ';
  const slider = document.createElement('input'); slider.type = 'range'; slider.min = '0'; slider.max = '1'; slider.step = '0.01';
  slider.value = String(audio.musicVolume); slider.setAttribute('aria-label', 'music volume');
  slider.oninput = () => audio.setMusicVolume(Number(slider.value)); label.append(slider);
  const mute = document.createElement('button'); mute.textContent = audio.musicMuted ? 'Unmute music' : 'Mute music';
  mute.onclick = () => { audio.setMusicMuted(!audio.musicMuted); mute.textContent = audio.musicMuted ? 'Unmute music' : 'Mute music'; };
  const control = document.createElement('div'); control.append(label, mute);
  const leave = () => menu.show(back());
  menu.show({ id: 'audio', title: 'Audio', text: null, control, back: leave,
    items: [{ id: 'back', label: 'Back', detail: null, enabled: true, run: leave }] });
}
