import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import type { PerfSnapshot } from '../../src/render/performance';

test('1440p fixed street path stays inside the backend budget', async ({ page }, info) => {
  const backend = info.project.name.replace(/-perf$/, '');
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  const tiles = process.env.PERF_TILES ?? 'dev-tiles/tileset.json';
  const quality = process.env.PERF_QUALITY ?? 'medium';
  await page.goto(`/?backend=${backend}&quality=${quality}&tiles=${encodeURIComponent(tiles)}` +
    '&cam=23459658,6704254,3.7,35,-3,60&time=2026-10-15T11:00:00Z');
  await page.waitForFunction(() => window.__qaReady || window.__qaError);
  expect(await page.evaluate(() => window.__qaError)).toBeUndefined();
  await page.evaluate(async () => {
    window.__render!.beforeFrame = () => false;
    await window.__render!.perf.completed();
    await window.__render!.perf.flush();
  });

  const measurement = await page.evaluate(() => new Promise<{ stats: PerfSnapshot; drainedMsPerFrame: number }>((resolve, reject) => {
    const render = window.__render!;
    const warm = 120, measured = 240;
    let frame = 0;
    let started = 0;
    let glPending = false;
    const webgl = render.perf.snapshot().backend === 'WebGL2';
    render.beforeFrame = () => {
      if (glPending) return false;
      const t = (frame < warm ? frame / warm : (frame - warm) / measured);
      render.camera.position.set(158 + 28 * t, 3.7, 252 - 22 * t);
      render.camera.lookAt(192, 8, 209);
      render.camera.updateMatrixWorld();
      if (frame === warm) { render.perf.reset(); started = performance.now(); }
      return true;
    };
    render.afterFrame = () => {
      if (++frame < warm + measured) {
        // ANGLE elapsed-time queries span pipelined draw stages. Fence each WebGL frame to avoid attributing
        // queued frames' latency to one query. WebGPU has explicit begin/end timestamp markers instead.
        if (webgl) {
          glPending = true;
          void render.perf.completed().then(() => { glPending = false; }, reject);
        }
        return;
      }
      render.renderer.setAnimationLoop(null);
      delete render.beforeFrame; delete render.afterFrame;
      // Drain the entire submitted route, including readbacks. Fast CPU submission alone cannot pass throughput.
      void render.perf.completed().then(() => render.perf.flush()).then(() => resolve({
        stats: render.perf.snapshot(), drainedMsPerFrame: (performance.now() - started) / measured,
      }), reject);
    };
  }));
  const { stats, drainedMsPerFrame } = measurement;
  const asset = await page.evaluate(async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`tileset HTTP ${response.status}`);
    const body = await response.arrayBuffer();
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', body))].map(v => v.toString(16).padStart(2, '0')).join('');
    const manifestResponse = await fetch(new URL('manifest.json', response.url));
    if (!manifestResponse.ok) throw new Error(`manifest HTTP ${manifestResponse.status}`);
    const manifestBody = await manifestResponse.arrayBuffer();
    const manifestHash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', manifestBody))]
      .map(v => v.toString(16).padStart(2, '0')).join('');
    const manifest = JSON.parse(new TextDecoder().decode(manifestBody));
    return { url: response.url, tilesetSha256: hash, manifestSha256: manifestHash, visual: manifest.visual,
      qa: window.__qaInfo, game: window.__game?.qa };
  }, tiles);
  const commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const dirty = !!execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim();
  const report = { commit, dirty, date: new Date().toISOString(), browser: await page.evaluate(() => navigator.userAgent),
    host: { cpu: os.cpus()[0]?.model, cores: os.cpus().length, arch: os.arch(), loadAverage: os.loadavg() },
    dataset: tiles.startsWith('dev-tiles/') ? 'synthetic-dev-tiles' : 'configured-tiles', quality,
    path: 'street-v1: (158,3.7,252) to (186,3.7,230), lookAt(192,8,209)', warmFrames: 120, measuredFrames: 240,
    completion: backend === 'webgl' ? 'per-frame GL fence + end-of-route drain' : 'end-of-route GPU drain', drainedMsPerFrame,
    budgetMs: backend === 'webgpu' ? 16.6 : 22, stats, asset, errors };
  const dir = path.join(reportsRoot, 'perf', `${commit}${dirty ? '-dirty' : ''}`);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${backend}-${quality}.json`);
  fs.writeFileSync(file, JSON.stringify(report, null, 2));
  await info.attach('performance', { path: file, contentType: 'application/json' });
  await page.screenshot({ path: path.join(dir, `${backend}-${quality}.png`) });
  console.log(JSON.stringify(report));

  expect(errors).toEqual([]);
  expect(stats.backend).toBe(backend === 'webgpu' ? 'WebGPU' : 'WebGL2');
  expect(stats.resolution).toEqual([2560, 1440]);
  expect(stats.totalFrames).toBe(240);
  expect(stats.drawCalls).toBeGreaterThan(5);
  expect(stats.triangles).toBeGreaterThan(100);
  expect(stats.cpuMs.p95).not.toBeNull();
  expect(stats.cpuMs.p95!).toBeLessThanOrEqual(report.budgetMs);
  expect(stats.frameMs.p95!).toBeLessThanOrEqual(report.budgetMs);
  expect(drainedMsPerFrame).toBeLessThanOrEqual(report.budgetMs);
  if (backend === 'webgpu') {
    expect(stats.gpuStatus).toBe('timestamp-query');
    expect(stats.gpuMs.samples).toBeGreaterThanOrEqual(216);
  }
  if (stats.gpuMs.samples) {
    expect(stats.gpuMs.samples).toBeGreaterThanOrEqual(216);
    expect(stats.gpuMs.p50!).toBeGreaterThan(0);
    expect(stats.gpuMs.p95!).toBeLessThanOrEqual(report.budgetMs);
    expect(stats.workMs.p95!).toBeLessThanOrEqual(report.budgetMs);
  }
});
