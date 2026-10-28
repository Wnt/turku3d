import type { OptionId, ShopId } from '../content/shops';
import type { OrderQuote } from './quotes';

export interface ShopOptionView {
  id: OptionId;
  label: string;
  detail: string | null;
  /** Euros the player pays (negative: the player is paid), or null when the option has no price. */
  price: number | null;
  icon: string | null;
  enabled: boolean;
  /** Why the option is greyed out, or null. */
  reason: string | null;
  quote: OrderQuote | null;
}

/** Everything the shop menu shows; texts are exact dialogue line texts. */
export interface ShopView {
  id: ShopId;
  title: string;
  speaker: string;
  /** The shopkeeper's latest line in this visit: the greeting, then each reply. */
  line: string;
  background: string;
  money: number;
  /** Delivery orders left on the courier permit. */
  permit: number;
  /** Pizzas handed over so far. */
  delivered: number;
  options: ShopOptionView[];
}

