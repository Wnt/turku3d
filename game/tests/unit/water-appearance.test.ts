import { describe, expect, it } from 'vitest';
import { waterColour, waterSharingKey } from '../../src/render/waterAppearance';

describe('water body appearance', () => {
  it('shares a body across tiles, but never mixes coplanar pool and river colours', () => {
    const pool = {water_body: 'pool', water_color: [.6, .8, .9]};
    const river = {water_body: 'river', water_color: [.3, .2, .1]};
    expect(waterSharingKey('0,1,0,0', pool)).toBe(waterSharingKey('0,1,0,0', {...pool}));
    expect(waterSharingKey('0,1,0,0', pool)).not.toBe(waterSharingKey('0,1,0,0', river));
    expect(waterSharingKey('0,1,0,0', pool)).not.toBe(waterSharingKey('0,1,0,2', pool));
    expect(waterSharingKey('0,1,0,0', pool)).not.toBe(waterSharingKey('0,1,0,0', {...pool, water_slope: true}));
    expect(waterColour(pool)).toEqual([.6, .8, .9]);
    expect(waterColour({water_color: [1, NaN, 0]})).toBeUndefined();
    expect(waterColour({water_color: [255, 128, 0]})).toBeUndefined();
  });
});
