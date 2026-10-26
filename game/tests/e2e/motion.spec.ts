import { isolatedTest as test, expect } from './fixtures';
import { PNG } from 'pngjs';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

type Sample = { frame: number; time: number; x: number; y: number };

test('turning around a moving car and walker keeps actor edges in one screen position', { tag: ['@drive', '@character', '@render'] }, async ({ page }, info) => {
  await page.addInitScript(() => {
    const w = window as any;
    w.pad = { id: 'Xbox Wireless Controller (virtual)', index: 0, connected: true, mapping: 'standard',
      axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })), timestamp: 0 };
    Object.defineProperty(navigator, 'getGamepads', { value: () => [w.pad] });
  });
  const axis = (index: number, value: number) => page.evaluate(([i, v]) => { (window as any).pad.axes[i] = v; }, [index, value]);
  const button = (index: number, value: number) => page.evaluate(([i, v]) => {
    (window as any).pad.buttons[i] = { value: v, pressed: v > 0.5, touched: v > 0 };
  }, [index, value]);
  await page.goto(`/?backend=${info.project.name}&aa=taa&hud=0`);
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  await page.getByRole('button', { name: 'Start playing' }).click();
  await axis(0, 1);
  await page.waitForFunction(() => { const s = (window as any).__game.state();
    return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 3; });
  await axis(0, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await button(3, 0);

  const record = async (mode: 'car' | 'foot'): Promise<{ samples: Sample[]; image: string }> => page.evaluate(async mode => {
    const w = window as any;
    const samples: Sample[] = [];
    const project = (p: number[]) => {
      const camera = w.__render.camera;
      const m = camera.matrixWorldInverse.elements, q = camera.projectionMatrix.elements;
      const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
      const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
      const z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
      const clipX = q[0] * x + q[4] * y + q[8] * z + q[12];
      const clipY = q[1] * x + q[5] * y + q[9] * z + q[13];
      const clipW = q[3] * x + q[7] * y + q[11] * z + q[15];
      return [(clipX / clipW + 1) * innerWidth / 2, (1 - clipY / clipW) * innerHeight / 2];
    };
    const image = await new Promise<string>(resolve => {
      w.__render.afterFrame = () => {
        const state = w.__game.state();
        const p = mode === 'car' ? state.visualVehicle : state.visualFeet;
        const [x, y] = project([p[0], p[1] + (mode === 'car' ? 0.9 : 1.6), p[2]]);
        samples.push({ frame: samples.length, time: performance.now(), x, y });
        if (samples.length >= 90) {
          w.__render.afterFrame = undefined;
          resolve(w.__render.renderer.domElement.toDataURL('image/png'));
        }
      };
    });
    return { samples, image };
  }, mode);
  await button(7, 0.35);
  await axis(2, 0.7);
  const { samples: car, image: carData } = await record('car');
  const carImage = PNG.sync.read(Buffer.from(carData.split(',')[1], 'base64'));
  await button(7, 0);
  await axis(2, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  await button(3, 0);
  await axis(0, 0.7);
  await axis(2, 0.7);
  const { samples: walker, image: walkerData } = await record('foot');
  const walkerImage = PNG.sync.read(Buffer.from(walkerData.split(',')[1], 'base64'));
  const shots = path.join(reportsRoot, 'screenshots');
  fs.mkdirSync(shots, { recursive: true });
  fs.writeFileSync(path.join(shots, `${info.project.name}-motion-walker.png`), PNG.sync.write(walkerImage));
  await axis(0, 0);
  await axis(2, 0);

  for (const [name, samples] of [['car', car], ['walker', walker]] as const) {
    const jumps = samples.slice(2).map((s, i) => Math.hypot(s.x - 2 * samples[i + 1].x + samples[i].x,
      s.y - 2 * samples[i + 1].y + samples[i].y));
    const p95 = jumps.sort((a, b) => a - b)[Math.ceil(jumps.length * 0.95) - 1];
    console.log(info.project.name, name, 'p95 acceleration', p95, 'max horizontal offset', Math.max(...samples.map(s => Math.abs(s.x - 640))));
    expect(p95, `${name} screen-position acceleration`).toBeLessThan(name === 'car' ? 3 : 10);
    if (name === 'car') expect(Math.max(...samples.map(s => Math.abs(s.x - 640))), 'car and chase camera disagree on render pose').toBeLessThan(1.5);
  }
  // Pale red/green pixels separated from the solid silhouettes indicate stale TAA history.
  for (const [name, image, color] of [['car', carImage, 'red'], ['walker', walkerImage, 'green']] as const) {
    const strong: number[] = [], faint: number[] = [];
    const area = color === 'red' ? { x0: 350, x1: 930, y0: 240, y1: 640 } : { x0: 550, x1: 800, y0: 320, y1: 510 };
    for (let y = area.y0; y < area.y1; y++) for (let x = area.x0; x < area.x1; x++) {
      const i = (y * image.width + x) * 4;
      const [r, g, b] = image.data.subarray(i, i + 3);
      const primary = color === 'red' ? r : g, secondary = color === 'red' ? g : r;
      if (primary > secondary * 1.16 && primary > b * (color === 'red' ? 1.08 : 0.85)) {
        if (primary - secondary > (color === 'red' ? 18 : 12)) strong.push(x);
        // Faint blue pavement can satisfy the broad visibility mask; a green
        // history fringe still needs more green than blue at its edge.
        else if (color === 'red' || g >= b) faint.push(x);
      }
    }
    expect(strong.length, `${name} visible color`).toBeGreaterThan(1000);
    const left = Math.min(...strong), right = Math.max(...strong);
    const separated = faint.filter(x => x < left - 5 || x > right + 5).length;
    console.log(info.project.name, name, 'strong pixels', strong.length, 'separated faint pixels', separated);
    expect(separated, `separated TAA ${name} outline`).toBeLessThan(30);
  }
});
