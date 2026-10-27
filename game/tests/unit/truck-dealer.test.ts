import { describe, expect, it } from 'vitest';
import { VEHICLE_PRICE, parSeconds } from '../../src/gameplay/economy';
import { lineText } from '../../src/gameplay/content/dialogue';
import { DEALER, MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { OWN_VEHICLE_ID, TRUCK_ID, ownedVehicleId } from '../../src/gameplay/missions/missions';
import { parseSave } from '../../src/gameplay/missions/save';
import { harness, lDistance, near } from './missionHarness';

describe('trophy truck at the dealer', () => {
  it('sells the trophy truck for 1000 €: its own physics vehicle, its line, its par speed', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    h.press(near(DEALER.point));
    expect(h.option('buy_truck')).toMatchObject({ label: 'Trophy truck', icon: 'icon_truck', price: 1000, enabled: false,
      reason: lineText('reason_no_money') });
    h.leave(DEALER.point);
    h.earn(VEHICLE_PRICE.truck);
    const money = h.missions.money;
    expect(h.choose(DEALER.point, 'buy_truck').bought).toBe('truck');
    expect(h.missions).toMatchObject({ vehicle: 'truck', money: money - VEHICLE_PRICE.truck, shop: null });
    expect(h.last().message).toBe(lineText('dealer_sold_truck'));
    expect(ownedVehicleId('truck')).toBe(TRUCK_ID);
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: OWN_VEHICLE_ID });
    expect(h.missions.phase).not.toBe('carrying');
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: TRUCK_ID });
    expect(h.missions.phase).toBe('carrying');
    expect(h.missions.snapshot().par).toBeCloseTo(parSeconds(lDistance(PIZZERIA.point, h.missions.currentStop.point), 'truck'));
    expect(h.missions.mayDrive(TRUCK_ID)).toBe(true);
    expect(h.missions.mayDrive(OWN_VEHICLE_ID)).toBe(false);
  });

  it('keeps an owned truck in the save; every save version accepts the value', () => {
    const v2 = { version: 2, phase: 'at_pizzeria', money: 40, permit: 3, plates: false, kidney: false, warning: false,
      vehicle: 'truck', delivery: 4, elapsed: 0, par: 0 };
    const h = harness(JSON.stringify(v2));
    expect(h.missions).toMatchObject({ vehicle: 'truck', money: 40, permit: 3 });
    const v3 = JSON.parse(h.missions.serialize());
    expect(v3).toMatchObject({ version: 3, vehicle: 'truck' });
    expect(harness(JSON.stringify(v3)).missions.vehicle).toBe('truck');
    expect(parseSave(JSON.stringify(v3), 1).vehicle).toBe('truck');
    expect(parseSave(JSON.stringify({ ...v3, vehicle: 'monster-truck' }), 1).vehicle).toBe('none');
  });

});
