/** Authored wardrobe/body recipes: a stable model identity independent of local rendering or weather. */
import wardrobe from './wardrobe.json';
import { hash32 } from './network';

export interface HumanRecipe {
  id: string;
  family: number;
  age: number;
  height: number;
  shape: readonly [number, number, number, number];
}

/** 12 clothing/hair meshes × 8 anatomical shapes. Colours are additional variation, never counted as models. */
export const HUMAN_MODELS: readonly HumanRecipe[] = wardrobe.families.flatMap((family, familyIndex) =>
  wardrobe.bodies.map(body => ({ id: `${family.id}-${body.id}`, family: familyIndex,
    age: Math.max(body.age, family.minimumAge ?? 0), height: body.height, shape: body.shape as [number, number, number, number] })));

const PALETTE = {
  // A local population includes a range of skin tones. Repeated entries control artistic frequency only.
  skin: [0xdab6a0, 0xd2a98b, 0xc79e7c, 0xd6ae97, 0xb88d6d, 0x956548, 0x704b39, 0xd9b59a],
  top: [0x303847, 0x353638, 0xe1ded5, 0x7f383b, 0x536550, 0xc3b28f, 0x567b94, 0x9b9b94, 0xb3694d, 0x6b5c4f, 0xc4a147, 0x597b75],
  bottom: [0x35465d, 0x536b88, 0x303036, 0x666762, 0xa0977e, 0x526179],
  hair: [0x463629, 0x6c5034, 0x9c825a, 0xb9a582, 0x312a26, 0x826846, 0x6e4030, 0xa4906d],
  olderHair: [0xb2aba2, 0x9c9992, 0xc6c1b7, 0x7c7975],
  shoes: [0x343333, 0x6a5140, 0xdbd8cc, 0x4e5358],
  inner: [0xe6e4de, 0xb7cbd6, 0x4b4d50, 0xd3c3a3, 0x925760],
};
const pick = (id: number, salt: number, list: readonly number[]) => list[hash32(id, salt) % list.length];

export function humanAppearance(id: number) {
  const model = HUMAN_MODELS[hash32(id, 8) % HUMAN_MODELS.length];
  const skin = pick(id, 20, PALETTE.skin);
  const top = pick(id, 21, PALETTE.top);
  return { model, variant: model.family, skin, top,
    bottom: pick(id, 22, PALETTE.bottom),
    hair: pick(id, 23, model.age >= 60 ? PALETTE.olderHair : PALETTE.hair),
    shoes: pick(id, 24, PALETTE.shoes), inner: model.family === 2 || model.family === 11 ? top : pick(id, 25, PALETTE.inner),
    legwear: skin };
}
