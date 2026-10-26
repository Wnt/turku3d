import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { settings, fingerprint, repo } from '../../../tools/dev/play.mjs';
import { execFileSync } from 'node:child_process';
import type { FullConfig, FullResult, Reporter, TestCase, TestResult, TestError } from '@playwright/test/reporter';
import { renderReport } from '../../tools/playtest-report.mjs';

export default class PlaytestReporter implements Reporter {
  private rows: Array<{ title: string; backend: string | undefined; status: string; [key: string]: unknown }> = [];
  private errors: string[] = [];
  private config!: FullConfig;
  constructor(private options: { outputDir: string }) {}
  onBegin(config: FullConfig) {
    this.config = config;
    fs.mkdirSync(path.join(this.options.outputDir, 'media'), { recursive: true });
  }
  onTestEnd(test: TestCase, result: TestResult) {
    const attachments = result.attachments.map((a, index) => {
      const ext = a.contentType === 'image/png' ? '.png' : a.contentType === 'video/webm' ? '.webm' : a.contentType === 'application/json' ? '.json' : '.zip';
      const name = `${test.id.replace(/[^a-zA-Z0-9-]/g, '_')}-${result.retry}-${index}${ext}`;
      const relative = `media/${name}`;
      const target = path.join(this.options.outputDir, relative);
      if (a.path) fs.copyFileSync(a.path, target);
      else if (a.body) fs.writeFileSync(target, a.body);
      return { name: a.name, type: a.contentType, url: relative };
    });
    const metrics = result.attachments.find(a => a.name === 'route-metrics');
    const evidence = metrics?.body ? JSON.parse(metrics.body.toString()) : null;
    this.rows.push({ title: test.title, backend: test.parent.project()?.name, tags: test.tags, status: result.status,
      duration: result.duration, errors: result.errors.map(e => e.message ?? String(e)), attachments, evidence });
  }
  onError(error: TestError) { this.errors.push(error.message ?? String(error)); }
  onEnd(result: FullResult) {
    let commit = 'unknown', dirty = true;
    try {
      commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
      dirty = !!execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim();
    } catch { /* Reports must survive missing Git metadata. */ }
    const s = settings();
    const provenance = {
      tileManifestSha256: createHash('sha256').update(fs.readFileSync(path.join(s.tiles, 'manifest.json'))).digest('hex'),
      testsSha256: fingerprint(path.join(repo, 'game'), ['tests/e2e', 'tests/e2e-playtest', 'playwright.playtest.config.ts', 'tools/playtest-report.mjs']),
      gameCache: JSON.parse(fs.readFileSync(path.join(s.cache, 'game.json'), 'utf8')),
    };
    const buildingIds = ['103295893K', '103295888D', '103295899S', '103295889E', '1032958849'];
    const buildingsCovered = ['webgpu', 'webgl'].every(backend => buildingIds.every(id => this.rows.some(row =>
      row.backend === backend && row.title.startsWith(`AOI building ${id}:`) && row.status === 'passed')));
    const curbsCovered = ['webgpu', 'webgl'].every(backend => ['AOI curb: Xbox walks', 'AOI curb: Xbox car'].every(title =>
      this.rows.some(row => row.backend === backend && row.title.startsWith(title) && row.status === 'passed')));
    const report = { provenance, date: new Date().toISOString(), status: result.status, duration: result.duration, commit, dirty,
      selection: [this.config.grep].flat().map(String), rows: this.rows, errors: this.errors,
      gaps: ['Real-AOI full walk around the office block', 'Real-AOI stair ascent and descent',
        ...(!curbsCovered ? ['Real-AOI measured curb crossing and car strike'] : []),
        ...(!buildingsCovered ? ['Walk into five distinct real buildings and remain outside'] : []),
        'Drive the office block and collide with a building and the quay wall'],
    };
    fs.writeFileSync(path.join(this.options.outputDir, 'report.json'), JSON.stringify(report, null, 2));
    fs.writeFileSync(path.join(this.options.outputDir, 'index.html'), renderReport(report));
  }
}
