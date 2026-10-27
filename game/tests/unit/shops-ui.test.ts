import { expect, test } from 'vitest';
import { formatPrice, optionDetail } from '../../src/ui/shopMenu';
import { LABEL_RANGE, ZONE_RADIUS, zoneStrength } from '../../src/gameplay/zones';
import { INTERACT_RADIUS, type ShopOptionView } from '../../src/gameplay/missions/missions';

test('menu prices show payments, income with a plus sign, and nothing without a price', () => {
  expect(formatPrice(80)).toBe('80 €');
  expect(formatPrice(-400)).toBe('+400 €');
  expect(formatPrice(null)).toBe('');
});

test('the order option shows address, rounded route length and par time instead of a static detail', () => {
  const base: ShopOptionView = { id: 'take_order', label: 'Take', detail: null, price: null, icon: null, enabled: true,
    reason: null, quote: null };
  expect(optionDetail({ ...base, quote: { address: 'Linnankatu 13a', metres: 139.6, par: 119.7, stops: 1 } }))
    .toBe('Linnankatu 13a · 140 m · par 2:00');
  expect(optionDetail({ ...base, id: 'slice', detail: 'Full speed' })).toBe('Full speed');
});

test('zones are drawn just outside the interaction radius, pulse gently and fade out beyond label range', () => {
  expect(ZONE_RADIUS).toBeGreaterThan(INTERACT_RADIUS);
  const samples = Array.from({ length: 32 }, (_, i) => zoneStrength(10, i * 0.05));
  expect(Math.min(...samples)).toBeGreaterThan(0.5);
  expect(Math.max(...samples)).toBeLessThanOrEqual(1);
  expect(Math.max(...samples) - Math.min(...samples)).toBeGreaterThan(0.2);
  expect(zoneStrength(LABEL_RANGE, 0)).toBeGreaterThan(0.3);
  expect(zoneStrength(80, 0)).toBe(0);
});
