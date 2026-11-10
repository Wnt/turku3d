// The pause menu's Graphics page: the quality presets (render/quality.ts MENU_TIERS). The choice is stored
// (localStorage, PRESET_KEY) and is the tier of every later load unless the page's `?quality=` names one.
import { MENU_TIERS, type QualityTier } from '../render/quality';
import type { MenuHost, MenuPage } from './menu';

export interface GraphicsControl {
  /** The preset in use now. */
  current(): QualityTier;
  /** Stores and applies a preset. */
  choose(tier: QualityTier): void;
  /** A line under the title (what the last choice did), and what a reload would still apply (null: nothing waits). */
  status(): { text: string | null; reload: string | null };
  /** Reloads the page into the stored preset, keeping the session (the room is rejoined, the game continues). */
  reload(): void;
}

const NAMES: Record<typeof MENU_TIERS[number], [label: string, detail: string]> = {
  low: ['Low', 'For slow GPUs: short tree and grass ranges, no lamp light pools'],
  reduced: ['Reduced', "Medium's look at 1080p, trees to impostors sooner"],
  medium: ['Medium', 'The default: 1440p, three shadow cascades'],
  high: ['High', 'Ambient occlusion, four shadow cascades, longer tree and grass ranges'],
  ultra: ['Ultra', 'For screenshots: native resolution, full detail to 1 km, loading before frame rate'],
};
export const presetName = (tier: QualityTier) => (NAMES as Record<string, [string, string] | undefined>)[tier]?.[0] ?? tier;

export function graphicsPage(menu: MenuHost, control: GraphicsControl, back: () => MenuPage, select?: string): MenuPage {
  const leave = () => menu.show(back());
  const current = control.current(), status = control.status();
  return { id: 'graphics', title: 'Graphics', text: status.text, back: leave, select: select ?? current, items: [
    ...MENU_TIERS.map(tier => ({ id: tier, label: tier === current ? `${NAMES[tier][0]} · current` : NAMES[tier][0], detail: NAMES[tier][1], enabled: true,
      run: () => { if (tier !== control.current()) control.choose(tier); menu.show(graphicsPage(menu, control, back, tier)); } })),
    ...(status.reload ? [{ id: 'reload', label: 'Reload to apply everything', detail: status.reload, enabled: true, run: () => control.reload() }] : []),
    { id: 'back', label: 'Back', detail: null, enabled: true, run: leave },
  ] };
}
