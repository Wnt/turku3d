// Localhost guidance replay over real city OSM metadata. Does not build tiles or simulate vehicle physics.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [base = 'http://localhost:5300', metadata, output = path.join(reportsRoot, 'route-guidance')] = process.argv.slice(2);
fs.mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true,
  ignoreDefaultArgs: chromeIgnoreDefaultArgs, args: chromeArgs([]) });
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  // A minimal page loads the production router/minimap without requiring a renderer or geometry build.
  await page.route('**/route-guidance-probe', route => route.fulfill({ contentType: 'text/html', body: '<body style="background:#17242c"></body>' }));
  if (metadata) await page.route('**/coarse/navigation.json', route => route.fulfill({ contentType: 'application/json', path: metadata }));
  await page.goto(`${base}/route-guidance-probe`);
  const result = await page.evaluate(async () => {
    const { parseRoadGraph } = await import('/src/gameplay/roadGraph.ts');
    const { StreetRouter } = await import('/src/gameplay/router.ts');
    const { RouteTracker, routeView } = await import('/src/gameplay/routeTracker.ts');
    const { Minimap } = await import('/src/gameplay/minimap.ts');
    const metadata = await fetch('/coarse/navigation.json').then(r => { if (!r.ok) throw Error(`navigation HTTP ${r.status}`); return r.json(); });
    const graph = parseRoadGraph(metadata.roads), router = new StreetRouter(graph);
    router.mode = 'car';
    const start = { x: 168, z: 246 };
    const landmarks = metadata.addresses.addresses.filter(a => a.landmark);
    if (landmarks.length < 5) throw Error('landmark metadata missing');
    const rows = [];
    for (const address of landmarks) {
      const goal = { x: address.point[0], z: address.point[1] };
      const path = router.route(start, goal);
      if (!path) throw Error(`no office route to ${address.address}`);
      const target = { id: address.id, label: address.address, point: goal, kind: 'dropoff' };
      const tracker = new RouteTracker(router);
      let calls = 0;
      const route = router.route.bind(router);
      router.route = (...args) => { calls++; return route(...args); };
      tracker.update(start, target, 'car');
      let previous = tracker.length, seams = 0, samples = 0, tile = '';
      const originalEnd = tracker.route.at(-1);
      for (let i = 1; i < path.length; i++) {
        const a = path[i - 1], b = path[i], n = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.z - a.z) / 7));
        for (let j = 1; j <= n; j++) {
          const p = { x: a.x + (b.x - a.x) * j / n, z: a.z + (b.z - a.z) * j / n };
          const next = `${Math.floor(p.x / 250)},${Math.floor(p.z / 250)}`;
          if (tile && next !== tile) { seams++; tracker.invalidate(); }
          tile = next;
          tracker.update(p, target, 'car');
          if (!tracker.route || tracker.length > previous + .01) throw Error(`route jumped at ${next}: ${address.address}`);
          if (JSON.stringify(tracker.route.at(-1)) !== JSON.stringify(originalEnd)) throw Error('destination changed');
          previous = tracker.length; samples++;
        }
      }
      router.route = route;
      const fit = routeView(start, path, 650);
      if (path.some(p => Math.abs(p.x - fit.centre.x) > fit.span / 2 || Math.abs(p.z - fit.centre.z) > fit.span / 2)) throw Error('expanded map crops route');
      rows.push({ id: address.id, address: address.address, metres: router.distance(path), seams, samples, plans: calls, remaining: tracker.length });
      if (/Kural|Forum/i.test(address.address)) {
        const minimap = new Minimap(document.body, graph, router);
        minimap.setSource({ subscribe(fn) { fn({ targets: [target], activeTargetId: target.id, message: address.address, money: 0, timerSeconds: null }); return () => {}; } });
        minimap.toggleExpanded();
        minimap.update({ position: start, headingDeg: 0, facingDeg: 0, mode: 'car', nearEdge: false });
        minimap.element.style.cssText = 'display:inline-block;vertical-align:top;margin:12px;color:white';
      }
    }
    return { kind: 'city routing and guidance replay (no vehicle physics)', nodes: graph.nodes.length / 2, edges: graph.edges.length, routes: rows };
  });
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  await page.screenshot({ path: path.join(output, 'routes.png'), fullPage: true });
  assert.ok(result.routes.every(r => r.plans === 1), 'on-route progress must not replan');
  assert.ok(result.routes.every(r => r.remaining < 10), 'must reach each route end');
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
