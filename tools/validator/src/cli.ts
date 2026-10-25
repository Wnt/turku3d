// CLI: node src/cli.ts [options] <tile.col | dir> ...
// Exit codes: 0 all checks pass (skipped checks do not fail unless --require), 1 a check failed,
// 2 usage / load error.

import { writeFileSync } from 'node:fs';
import { parseArgs } from 'node:util';
import { CHECKS, DEFAULT_OPTIONS, validate } from './validate.ts';
import type { CheckName, Options } from './validate.ts';
import { htmlReport } from './report.ts';
import { DEFAULT_KCC } from './kcc.ts';

const USAGE = `usage: node src/cli.ts [options] <tile.col|dir>...
  --footprints F.geojson   building footprints (Polygon/MultiPolygon, engine [x,z]; or "turku3d_coords":"en")
  --curbs F.geojson        curb lines (LineString); default: stations from curb/quay/wall faces in the tiles
  --layers DIR            read validation sidecars from layer MANIFEST.json files
  --stairs flights.json   layer stair list: [{id,bottom:[x,y,z],top:[x,y,z],width}]
  --workers N (4)         parallel collision-tile workers (1 = serial)
  --html report.html      write an offline failure map (also written beside --out)
  --ortho image.png       georeferenced backdrop; requires --ortho-bounds x0,z0,x1,z1
  --out report.json        write the JSON report (default: stdout summary only)
  --checks a,b             subset of ${CHECKS.join(',')}
  --require a,b            these checks must not be 'skipped' (e.g. buildings for real tiles)
  --walkers N (2000)  --walk-seconds S (10)  --seed N (1)
  --ray-density R (2 rays/m², = 1 ray per 0.5 m²)  --drop-spacing M (5)  --curb-spacing M (2)
  --curb-speeds 1.5,4  --climb-required 0.25  --block-required 0.35  --stair-max-slope 40
  --kcc-mode probe|split (probe)  --autostep 0.22  --autostep-min-width 0.15  --snap 0.3
  --aoi x0,z0,x1,z1        restrict holes/drops/walkers to this engine-xz box
  --quiet                  no summary on stdout`;

function nums(s: string): number[] {
  return s.split(',').map(Number);
}

async function main(): Promise<number> {
  let a;
  try {
    a = parseArgs({
      allowPositionals: true,
      options: {
        layers: { type: 'string' }, stairs: { type: 'string' }, workers: { type: 'string' },
        html: { type: 'string' }, ortho: { type: 'string' }, 'ortho-bounds': { type: 'string' },
        footprints: { type: 'string' }, curbs: { type: 'string' }, out: { type: 'string' },
        checks: { type: 'string' }, require: { type: 'string' },
        walkers: { type: 'string' }, 'walk-seconds': { type: 'string' }, seed: { type: 'string' },
        'ray-density': { type: 'string' }, 'drop-spacing': { type: 'string' }, 'curb-spacing': { type: 'string' },
        'curb-speeds': { type: 'string' }, 'climb-required': { type: 'string' }, 'block-required': { type: 'string' },
        'stair-max-slope': { type: 'string' }, 'kcc-mode': { type: 'string' }, autostep: { type: 'string' },
        'autostep-min-width': { type: 'string' }, snap: { type: 'string' }, aoi: { type: 'string' },
        quiet: { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
      },
    });
  } catch (e: any) {
    console.error(e.message + '\n' + USAGE);
    return 2;
  }
  const v = a.values;
  if (v.help || !a.positionals.length) {
    console.error(USAGE);
    return v.help ? 0 : 2;
  }
  const checks = v.checks ? (v.checks.split(',') as CheckName[]) : undefined;
  for (const c of [...(checks ?? []), ...(v.require?.split(',') ?? [])]) if (!(CHECKS as readonly string[]).includes(c)) { console.error(`unknown check ${c}`); return 2; }
  const mode = (v['kcc-mode'] ?? 'probe') as 'probe' | 'split';
  if (mode !== 'probe' && mode !== 'split') { console.error('--kcc-mode must be probe or split'); return 2; }
  const opts: Options = {
    ...DEFAULT_OPTIONS,
    inputs: a.positionals,
    layers: v.layers, stairs: v.stairs, workers: Number(v.workers ?? 4),
    footprints: v.footprints,
    curbs: v.curbs,
    checks,
    seed: v.seed ? Number(v.seed) : DEFAULT_OPTIONS.seed,
    walkers: v.walkers ? Number(v.walkers) : DEFAULT_OPTIONS.walkers,
    walkSeconds: v['walk-seconds'] ? Number(v['walk-seconds']) : DEFAULT_OPTIONS.walkSeconds,
    rayDensity: v['ray-density'] ? Number(v['ray-density']) : DEFAULT_OPTIONS.rayDensity,
    dropSpacing: v['drop-spacing'] ? Number(v['drop-spacing']) : DEFAULT_OPTIONS.dropSpacing,
    curbSpacing: v['curb-spacing'] ? Number(v['curb-spacing']) : DEFAULT_OPTIONS.curbSpacing,
    curbSpeeds: v['curb-speeds'] ? nums(v['curb-speeds']) : DEFAULT_OPTIONS.curbSpeeds,
    climbRequired: v['climb-required'] ? Number(v['climb-required']) : DEFAULT_OPTIONS.climbRequired,
    blockRequired: v['block-required'] ? Number(v['block-required']) : DEFAULT_OPTIONS.blockRequired,
    stairMaxSlopeDeg: v['stair-max-slope'] ? Number(v['stair-max-slope']) : DEFAULT_OPTIONS.stairMaxSlopeDeg,
    kcc: {
      ...DEFAULT_KCC,
      mode,
      autostepMaxHeight: v.autostep ? Number(v.autostep) : DEFAULT_KCC.autostepMaxHeight,
      autostepMinWidth: v['autostep-min-width'] ? Number(v['autostep-min-width']) : DEFAULT_KCC.autostepMinWidth,
      snapToGround: v.snap ? Number(v.snap) : DEFAULT_KCC.snapToGround,
    },
    aoi: v.aoi ? (nums(v.aoi) as [number, number, number, number]) : undefined,
  };
  for (const key of ['workers', 'walkers', 'walkSeconds', 'rayDensity', 'dropSpacing', 'curbSpacing', 'climbRequired', 'blockRequired', 'stairMaxSlopeDeg'] as const) {
    if (!Number.isFinite(opts[key]) || opts[key]! <= 0) { console.error(`invalid ${key}: expected a positive finite number`); return 2; }
  }
  if (!Number.isInteger(opts.workers) || !Number.isInteger(opts.walkers) || opts.curbSpeeds.some(n => !Number.isFinite(n) || n <= 0)) return 2;
  if (opts.aoi && (opts.aoi.length !== 4 || !opts.aoi.every(Number.isFinite) || opts.aoi[2] <= opts.aoi[0] || opts.aoi[3] <= opts.aoi[1])) return 2;
  let report: any;
  try {
    report = await validate(opts);
  } catch (e: any) {
    console.error(`validator error: ${e.stack ?? e}`);
    return 2;
  }
  const required = v.require ? v.require.split(',') : [];
  for (const r of required) {
    if (report.checks[r]?.status === 'skipped' || !report.checks[r]) {
      report.pass = false;
      report.checks[r] = { ...report.checks[r], status: 'fail', reason: 'required check has no testable layer input', originalStatus: report.checks[r]?.status ?? 'absent' };
      report.summary[r] = 'fail';
      report.requiredMissing = [...(report.requiredMissing ?? []), r];
    }
  }
  try {
    if (v.out) writeFileSync(v.out, JSON.stringify(report, null, 1) + '\n');
    const html = v.html ?? (v.out ? v.out.replace(/\.json$/, '') + '.html' : undefined);
    if (html) writeFileSync(html, htmlReport(report, v.ortho, v['ortho-bounds'] ? nums(v['ortho-bounds']) : undefined));
  } catch (error) { console.error(`report output error: ${error}`); return 2; }
  if (!v.quiet) {
    console.log(`tiles: ${report.tiles.map((t: any) => t.id ?? t.file).join(' ')}  rapier ${report.rapier}  ${report.elapsedMs} ms`);
    for (const [k, c] of Object.entries(report.checks) as [string, any][]) {
      const extra = c.originalStatus ? c.reason : c.perTile ? `${c.perTile.filter((t: any) => t.status === 'fail').length}/${c.perTile.length} tiles failed`
        : k === 'holes' ? `rays=${c.rays} misses=${c.misses}`
        : k === 'buildings' ? (c.status === 'skipped' ? c.reason : `walkers=${c.walkers} insideAnyFrame=${c.insideAnyFrame} insideAtEnd=${c.insideAtEnd}`)
        : k === 'curbs' ? `stations=${c.stations} climb ${c.climbed}/${c.climbExpected} block ${c.blocked}/${c.blockExpected} info=${c.infoBand}`
        : k === 'stairs' ? (c.status === 'skipped' ? c.reason : `flights=${c.count} failed=${c.failed}`)
        : k === 'belowGround' ? `drops=${c.drops} void=${c.dropsWithoutSurface} belowSamples=${c.belowGroundSamples}/${c.trajectorySamples}`
        : k === 'format' ? `${c.errors.length} errors${c.errors.length ? ': ' + c.errors.slice(0, 3).join(' | ') : ''}` : '';
      console.log(`  ${k.padEnd(12)} ${c.status.toUpperCase().padEnd(8)} ${extra}`);
    }
    if (report.requiredMissing) console.log(`  required coverage missing: ${report.requiredMissing.join(',')}`);
    console.log(report.pass ? 'PASS' : 'FAIL');
  }
  return report.pass ? 0 : 1;
}

process.exitCode = await main();
