export const TZ = 'Europe/Helsinki';
export const HOUR_MS = 3_600_000;
const formatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset',
});

export function hourAt(now) {
  // Helsinki offsets and DST transitions are whole UTC hours. Flooring UTC keeps
  // the two autumn 03:00 hours distinct and skips the nonexistent spring 03:00.
  const start = Math.floor(now / HOUR_MS) * HOUR_MS;
  const p = Object.fromEntries(formatter.formatToParts(start).map(x => [x.type, x.value]));
  const offset = p.timeZoneName.replace('GMT', '').slice(0, 3);
  return {
    id: `${p.year}-${p.month}-${p.day}T${p.hour}${offset}`,
    start: new Date(start).toISOString(), end: new Date(start + HOUR_MS).toISOString(), tz: TZ,
  };
}
