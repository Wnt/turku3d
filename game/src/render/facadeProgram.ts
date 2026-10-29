// Program-sized windows reuse the kit's PBR cells; bands are painted in wall metres.
import { attribute, float, fract, max, min, mix, step, vec2 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';

function warp(t: Node<'float'>, lo: Node<'float'>, hi: Node<'float'>, a: Node<'float'>, b: Node<'float'>) {
  return t.lessThan(lo).select(t.div(max(lo, .001)).mul(a),
    t.greaterThan(hi).select(b.add(t.sub(hi).div(max(float(1).sub(hi), .001)).mul(float(1).sub(b))),
      a.add(t.sub(lo).div(max(hi.sub(lo), .001)).mul(b.sub(a)))));
}

export function programLayout(bays: Node<'vec2'>, metres: Node<'vec2'>, layout: Node<'vec3'>, upper: Node<'float'>, selected: Node<'float'>) {
  const opening = attribute('_kit_opening', 'vec4');
  const detail = attribute('_kit_detail', 'vec4');
  const size = attribute('_kit_extent', 'vec4').zw;
  const blank = layout.x.div(6).floor().add(42);
  const above = metres.y.greaterThanEqual(size.y);
  const plain = detail.z.greaterThan(.5).and(upper.lessThan(1));
  const module = layout.x.lessThan(42).and(above.or(plain)).select(blank, selected);
  const phase = bays.x.floor().mod(2).mul(2).sub(1).mul(detail.x);
  const x = fract(bays.x.add(phase)), y = fract(bays.y);
  const glass = layout.x.div(6).floor().equal(5);
  const sourceWidth = glass.select(.92, layout.x.mod(6).greaterThanEqual(3).select(.65, .45));
  const sourceHeight = glass.select(.72, mix(.426, .5, upper));
  const sourceSill = glass.select(.15, mix(.292, .21, upper));
  const horizontal = warp(x, float(.5).sub(opening.x.div(2)), float(.5).add(opening.x.div(2)),
    float(.5).sub(sourceWidth.div(2)), float(.5).add(sourceWidth.div(2)));
  const vertical = warp(y, opening.z, opening.z.add(opening.y), sourceSill, sourceSill.add(sourceHeight));
  // Doors and shopfronts keep their own complete atlas module at ground level.
  const special = upper.lessThan(1).and(module.mod(3).notEqual(0)).and(module.lessThan(42));
  const ordinary = special.or(module.greaterThanEqual(42)).select(vec2(fract(bays.x), y), vec2(horizontal, vertical));
  const patchY = metres.y.div(size.y).clamp(0, 1);
  const patch = detail.w.equal(2).select(vec2(x.sub(.5).mul(.49).add(.5), patchY.mul(.76)),
    vec2(x.sub(.5).mul(.45).add(.5), patchY.mul(.5).add(.21)));
  const local = detail.w.greaterThan(0).select(patch, ordinary);
  return { module, local, opening, detail, size, x, y };
}

export function programPigment(metres: Node<'vec2'>, bays: Node<'vec2'>, upper: Node<'float'>, layout: Node<'vec3'>,
  wall: Node<'vec3'>, trim: Node<'vec3'>, data: ReturnType<typeof programLayout>) {
  const bands = attribute('_kit_bands', 'vec4');
  const base = attribute('_kit_base', 'vec3');
  const plinth = step(metres.y, bands.x);
  const cornice = step(data.size.y.sub(bands.y), metres.y).mul(step(metres.y, data.size.y));
  const corner = step(min(metres.x, data.size.x.sub(metres.x)), bands.w);
  const floorDistance = min(fract(bays.y), float(1).sub(fract(bays.y))).mul(layout.z);
  const course = step(floorDistance, bands.z.div(2)).mul(upper);
  const dx = data.x.sub(.5).abs().mul(layout.y);
  const sill = data.opening.z, head = sill.add(data.opening.y);
  const dy = max(sill.sub(data.y), data.y.sub(head)).mul(layout.z);
  const surround = step(dx, data.opening.x.mul(layout.y).div(2).add(data.opening.w))
    .mul(step(dy, data.opening.w)).mul(upper)
    .mul(max(step(data.opening.x.mul(layout.y).div(2), dx), step(0, dy))).mul(float(layout.x.lessThan(42)));
  const balcony = step(dx, layout.y.mul(.36)).mul(step(data.y.mul(layout.z), 1.05))
    .mul(step(.15, data.y.mul(layout.z))).mul(upper).mul(data.detail.y)
    .mul(float(1).sub(bays.x.floor().mod(2))).mul(float(layout.x.lessThan(42)));
  const railing = balcony.mul(max(step(fract(metres.x.mul(5)), .12), step(.93, data.y.mul(layout.z))));
  const band = max(max(cornice, corner), max(course, surround)).mul(step(metres.y, data.size.y));
  const pigment = mix(base, wall, upper).mul(mix(1, .67, plinth));
  return { pigment, band, trim:mix(trim, trim.mul(.65), railing), balcony:balcony.mul(.15).add(railing.mul(.65)).clamp() };
}
