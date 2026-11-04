// Tree look A/B for Jonni's own judging (AGENTS.md rule 2). Owner: Jonni. Reason: Jonni judges crowns and leaves after
// the TRAA fix (e156e3ba); the 2026-11-04 treelook decisions were judged on shimmering crowns before it. Removal: once
// Jonni picks, the losing crown and leaf paths and this module go, at most ten merges after treelook2.
//
// Initial load: ?crowns=stretch|spread and ?leaves=full|single|alpha65. Live: the dev Layers panel (F4 / LB, ?dev=1 on
// a public host) switches both without a reload. No choice keeps the tier defaults: stretch on medium/high, spread on
// reduced/low; full leaves (double-sided cards, alpha test 0.5) everywhere.
import { crownMode, type CrownMode } from './crowns';

export const CROWN_MODES = ['stretch', 'spread'] as const satisfies readonly CrownMode[];
export type LeafVariant = 'full' | 'single' | 'alpha65';
export const LEAF_VARIANTS = ['full', 'single', 'alpha65'] as const satisfies readonly LeafVariant[];
export const LEAF_LABELS: Record<LeafVariant, string> = {
  full: 'full (double-sided, alpha 0.5)', single: 'single-sided cards', alpha65: 'alpha test 0.65',
};

export function parseCrowns(value: string | null): CrownMode | null {
  if (value === null || value === '') return null;
  if ((CROWN_MODES as readonly string[]).includes(value)) return value as CrownMode;
  throw new Error(`bad ?crowns= (stretch|spread): ${value}`);
}
export function parseLeaves(value: string | null): LeafVariant | null {
  if (value === null || value === '') return null;
  if ((LEAF_VARIANTS as readonly string[]).includes(value)) return value as LeafVariant;
  throw new Error(`bad ?leaves= (full|single|alpha65): ${value}`);
}

/** What applies a look to a loaded library (NatureLibrary.applyLook). */
export interface LookTarget { applyLook(crowns: CrownMode, leaves: LeafVariant): void }

/** The page's chosen crowns and leaves; null means the tier default. */
export class TreeLook {
  crowns: CrownMode | null;
  leaves: LeafVariant | null;
  private target: LookTarget | null = null;
  private tier = 'medium';
  constructor(search = typeof location === 'undefined' ? '' : location.search) {
    const params = new URLSearchParams(search);
    this.crowns = parseCrowns(params.get('crowns'));
    this.leaves = parseLeaves(params.get('leaves'));
  }
  setTier(tier: string) { this.tier = tier; }
  get crownMode(): CrownMode { return this.crowns ?? crownMode(this.tier); }
  get leafVariant(): LeafVariant { return this.leaves ?? 'full'; }
  attach(target: LookTarget | null) { this.target = target; target?.applyLook(this.crownMode, this.leafVariant); }
  /** Switch live; the choice also goes into the address so a reload keeps it. */
  set(crowns: CrownMode | null, leaves: LeafVariant | null) {
    this.crowns = crowns; this.leaves = leaves;
    this.target?.applyLook(this.crownMode, this.leafVariant);
    if (typeof location === 'undefined' || typeof history === 'undefined') return;
    const url = new URL(location.href);
    for (const [key, value] of [['crowns', crowns], ['leaves', leaves]] as const) {
      if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    }
    history.replaceState(history.state, '', url);
  }
  /** One line for the panel: the current choice and whether it is the default. */
  describe() {
    const crowns = `${this.crownMode}${this.crowns ? '' : ` (${this.tier} default)`}`;
    const leaves = `${this.leafVariant}${this.leaves ? '' : ' (default)'}`;
    return `Tree look A/B · crowns ${crowns} · leaves ${leaves}`;
  }
}

export const treeLook = new TreeLook();
