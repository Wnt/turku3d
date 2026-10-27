/** UTC minutes on the selected date; label includes Europe/Helsinki local time and date (DST-safe). */
export function timeControl(initial: Date, onChange: (date: Date) => void): HTMLLabelElement {
  const label = document.createElement('label');
  label.id = 'time-control';
  label.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:3;padding:8px;background:#101820cc;color:white;font:12px monospace';
  const text = document.createElement('span');
  const slider = document.createElement('input');
  slider.type = 'range'; slider.min = '0'; slider.max = '1439'; slider.step = '5';
  slider.setAttribute('aria-label', 'Time of day (UTC minutes)');
  slider.value = String(initial.getUTCHours() * 60 + initial.getUTCMinutes());
  slider.style.cssText = 'display:block;width:240px';
  const day = Date.UTC(initial.getUTCFullYear(), initial.getUTCMonth(), initial.getUTCDate());
  const formatter = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki', dateStyle: 'short', timeStyle: 'short' });
  const show = (date: Date) => { text.textContent = `${formatter.format(date)} Europe/Helsinki`; };
  show(initial);
  slider.addEventListener('input', () => show(new Date(day + Number(slider.value) * 60000)));
  // Commit on release: generating PMREM on every pointer move would stall the game.
  slider.addEventListener('change', () => onChange(new Date(day + Number(slider.value) * 60000)));
  label.append(text, slider); document.body.append(label);
  return label;
}
