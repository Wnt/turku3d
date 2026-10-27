import { MusicSystem, TRACKS, STEMS, DEFAULT_PARAMETERS, type MusicParameters, type MusicState } from '../../game/src/audio/music';
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const stored = localStorage.getItem('turku3d.music-lab');
let params: MusicParameters = structuredClone(DEFAULT_PARAMETERS);
try { if (stored) { const value = JSON.parse(stored); params = { ...params, ...value, layerGains: { ...params.layerGains, ...value.layerGains } }; } } catch { /* ignore invalid local settings */ }
let system: MusicSystem | null = null, context: AudioContext | null = null, simulation = 0;
const track = $<HTMLSelectElement>('track');
TRACKS.forEach((t, i) => track.add(new Option(t.name, String(i))));
const info = () => { const t = TRACKS[Number(track.value)]; $('track-info').textContent = `${t.style} · ${t.bpm} BPM · ${t.key}`; };
info(); track.onchange = () => { system?.selectTrack(Number(track.value)); info(); };
const save = () => { localStorage.setItem('turku3d.music-lab', JSON.stringify(params)); system?.setParameters(params); if (system && (['car', 'shop'].some(id => $<HTMLButtonElement>(id).getAttribute('aria-pressed') === 'true'))) system.setMuffled(params.muffleAmount); };
const sliders = $<HTMLDivElement>('sliders');
const add = (name: string, key: keyof Omit<MusicParameters, 'layerGains'>, min: number, max: number, step: number) => {
  const label = document.createElement('label'), out = document.createElement('output'), input = document.createElement('input');
  label.append(name, out, input); input.type = 'range'; input.min = String(min); input.max = String(max); input.step = String(step); input.value = String(params[key]); input.setAttribute('aria-label', name);
  const update = () => { out.textContent = input.value; params[key] = Number(input.value); save(); };
  input.oninput = update; out.textContent = input.value; sliders.append(label);
};
add('Master volume', 'volume', 0, 1, 0.01); add('Crossfade bars', 'crossfadeBars', 0.25, 8, 0.25);
add('Muffle cutoff (Hz)', 'muffleCutoff', 250, 6000, 50); add('Muffle amount', 'muffleAmount', 0, 1, 0.01);
add('Muffle smoothing (ms)', 'muffleMs', 20, 2000, 10); add('Hurry-up start', 'hurryStart', 0.1, 0.9, 0.01);
add('Tempo lift (%)', 'tempoLift', 0, 0.5, 0.01); add('Stinger lead-in (bars)', 'stingerLeadBars', 1, 4, 1);
for (const stem of STEMS) {
  const label = document.createElement('label'), out = document.createElement('output'), input = document.createElement('input');
  label.append(`${stem} gain`, out, input); input.type = 'range'; input.min = '0'; input.max = '1'; input.step = '0.01'; input.value = String(params.layerGains[stem]); input.setAttribute('aria-label', `${stem} gain`);
  input.oninput = () => { params.layerGains[stem] = Number(input.value); out.textContent = input.value; save(); };
  out.textContent = input.value; sliders.append(label);
}
$('start').onclick = () => {
  if (!context) { context = new AudioContext(); system = new MusicSystem(context); system.setParameters(params); system.selectTrack(Number(track.value)); window.musicLab = system; }
  system!.start(); $('start').textContent = 'Audio running';
};
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-state]')) button.onclick = () => {
  system?.setState(button.dataset.state as MusicState);
  document.querySelectorAll('[data-state]').forEach(b => b.classList.toggle('active', b === button));
};
const deadline = $<HTMLInputElement>('deadline'); deadline.oninput = () => { $('deadline-value').textContent = `${deadline.value}%`; system?.setDeadline(Number(deadline.value) / 100); };
const duration = $<HTMLInputElement>('duration'); duration.oninput = () => { $('duration-value').textContent = duration.value; };
$('simulate').onclick = () => { cancelAnimationFrame(simulation); deadline.value = '0'; deadline.dispatchEvent(new Event('input')); const start = performance.now(), seconds = Number(duration.value);
  const frame = () => { deadline.value = String(Math.min(100, Math.round((performance.now() - start) / (seconds * 10)))); deadline.dispatchEvent(new Event('input')); if (Number(deadline.value) < 100) simulation = requestAnimationFrame(frame); else system?.deliver(); }; simulation = requestAnimationFrame(frame); };
$('deliver').onclick = () => { cancelAnimationFrame(simulation); system?.deliver(); deadline.value = '0'; deadline.dispatchEvent(new Event('input')); };
const muffle = () => { const on = ['car', 'shop'].some(id => $<HTMLButtonElement>(id).getAttribute('aria-pressed') === 'true'); system?.setMuffled(on ? params.muffleAmount : 0); };
for (const id of ['car', 'shop']) $<HTMLButtonElement>(id).onclick = () => { const b = $<HTMLButtonElement>(id); b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true')); b.classList.toggle('active'); muffle(); };
$('copy').onclick = async () => { const json = JSON.stringify(params, null, 2); await navigator.clipboard.writeText(json); $('copied').textContent = json; };
function display() {
  if (system && context) { system.tick(); const s = system.snapshot(); $('status').textContent = `Bar ${s.bar} · Beat ${s.beat} · ${s.tempo.toFixed(1)} BPM · ${s.state}`; $('layers').textContent = `Layers: ${[...new Set(s.layers)].join(', ') || 'none'}`;
    const bins = new Uint8Array(system.analyser.frequencyBinCount); system.analyser.getByteFrequencyData(bins);
    let sum = 0, weighted = 0; for (let i = 0; i < bins.length; i++) { sum += bins[i]; weighted += bins[i] * i * context.sampleRate / system.analyser.fftSize; }
    const level = sum / bins.length / 255, centroid = sum ? weighted / sum : 0;
    ($('meter').firstElementChild as HTMLElement).style.width = `${Math.min(100, level * 500)}%`; $('metrics').textContent = `Level ${level.toFixed(3)} · centroid ${centroid.toFixed(0)} Hz`;
    window.musicMetrics = { level, centroid, state: s.state, tempo: s.tempo };
  }
  requestAnimationFrame(display);
}
display();
declare global { interface Window { musicLab: MusicSystem; musicMetrics: { level: number; centroid: number; state: MusicState; tempo: number }; } }
