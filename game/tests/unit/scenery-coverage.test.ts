import { expect, test } from 'vitest';
import { SceneryCoverage } from '../../src/render/sceneryCoverage';
import { regionalTiles } from '../../src/world/sceneryView';

test('regional terrain yields only to occupied world cells and returns after eviction', () => {
  const mask = new SceneryCoverage(), at = (i: number,j: number) => mask.pixels[(j+128)*256+i+128];
  mask.update(['t0_-1_0','t2_1_1']);
  expect(at(-1,0)).toBe(255); expect(at(0,0)).toBe(0);
  for (let i=4;i<8;i++) for(let j=4;j<8;j++) expect(at(i,j)).toBe(255);
  expect(at(8,4)).toBe(0); expect(at(4,8)).toBe(0);
  mask.update(['t0_0_0']); expect(at(-1,0)).toBe(0);expect(at(0,0)).toBe(255);
  mask.update([]); expect(mask.pixels.some(Boolean)).toBe(false);
  mask.texture.dispose();
});
test('terrain reserve covers every azimuth independently of the city view sector', () => {
  const ids=regionalTiles({x:125,z:125});
  for(const id of ['t4_0_0','t4_-1_0','t4_0_-1','t4_1_0','t4_0_1']) expect(ids).toContain(id);
  expect(ids.length).toBeLessThan(80);
});
