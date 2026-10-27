// The three shops a player can enter (docs/design/story.md, "Shops"): their zone, menu art and options in menu
// order. Labels and details are dialogue line ids; availability and effects live in missions/missions.ts.
import type { RouteTarget, TargetKind } from '../contract';
import { DEALER, MARKET, PIZZERIA } from './locations';

export type ShopId = 'market' | 'pizzeria' | 'dealer';
export type OptionId = 'rent_permit' | 'forged_plates' | 'sell_kidney' | 'expiry_warning' | 'take_order' | 'slice'
  | 'tip_rules' | 'buy_scooter' | 'buy_car' | 'sell_vehicle' | 'leave';

export interface ShopOption {
  id: OptionId;
  /** Icon file stem under public/assets/ui/ (null: no icon). */
  icon: string | null;
}

export interface Shop {
  id: ShopId;
  target: RouteTarget;
  /** Background illustration stem under public/assets/ui/. */
  background: string;
  options: readonly ShopOption[];
}

const option = (id: OptionId, icon: string | null): ShopOption => ({ id, icon });

export const SHOPS: Record<ShopId, Shop> = {
  market: { id: 'market', target: MARKET, background: 'bg_market', options: [
    option('rent_permit', 'icon_permit'), option('forged_plates', 'icon_plates'), option('sell_kidney', 'icon_kidney'),
    option('expiry_warning', 'icon_warning'), option('leave', null)] },
  pizzeria: { id: 'pizzeria', target: PIZZERIA, background: 'bg_pizzeria', options: [
    option('take_order', 'icon_pizza_box'), option('slice', 'icon_slice'), option('tip_rules', 'icon_coins'),
    option('leave', null)] },
  dealer: { id: 'dealer', target: DEALER, background: 'bg_dealer', options: [
    option('buy_scooter', 'icon_scooter'), option('buy_car', 'icon_car'), option('sell_vehicle', 'icon_coins'),
    option('leave', null)] },
};

/** Dialogue line ids for an option's label and its one-line detail (null for none). */
export const optionLabel = (id: OptionId) => `option_${id}`;
export const optionDetail = (id: OptionId) => (id === 'take_order' || id === 'leave' ? null : `detail_${id}`);

/** Floating zone label per target kind; every kind has exactly one place or the current drop-off. */
export const ZONE_LABELS: Record<TargetKind, string> = {
  market: 'zone_market', pickup: 'zone_pizzeria', shop: 'zone_dealer', dropoff: 'zone_dropoff',
};

/** Height (engine metres) from which a downward ray finds a zone's ground. The black market sits on the
 *  lower quay under the Auransilta deck, so its ray starts below the deck. */
export const ZONE_RAY_TOP: Partial<Record<string, number>> = { [MARKET.id]: 4 };
export const DEFAULT_ZONE_RAY_TOP = 40;
