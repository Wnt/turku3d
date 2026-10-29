// Program-sized windows reuse the kit's PBR cells; bands are painted in wall metres.
import { attribute, float, fract, max, min, mix, sqrt, step, vec2 } from 'three/tsl';
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
  const blank = layout.x.greaterThanEqual(42).select(layout.x, layout.x.div(6).floor().add(42));
  const above = metres.y.greaterThanEqual(size.y);
  const plain = detail.z.mod(2).greaterThan(.5).and(upper.lessThan(1));
  const selectedModule = layout.x.lessThan(42).and(above.or(plain)).select(blank, selected);
  const phase = bays.x.floor().mod(2).mul(2).sub(1).mul(detail.x);
  const x = fract(bays.x.add(phase)), y = fract(bays.y);
  const glass = layout.x.div(6).floor().equal(5);
  const sourceWidth = glass.select(.92, layout.x.mod(6).greaterThanEqual(3).select(.65, .45));
  const sourceHeight = glass.select(.72, mix(.426, .5, upper));
  const sourceSill = glass.select(.15, mix(.292, .21, upper));
  // detail.w: patch kind + 4 * head (1 window, 2 portal); negative heads
  // apply to sampled bays. Old flat v4 instructions keep their original meaning.
  const patchKind = detail.w.mod(4);
  const isPatch = detail.w.greaterThan(0);
  const shape = isPatch.select(detail.w.div(4).floor(),
    upper.lessThan(1).select(detail.z.div(2).floor(), detail.w.negate())).mul(float(layout.x.lessThan(42)));
  const special = upper.lessThan(1).and(selectedModule.mod(3).notEqual(0)).and(selectedModule.lessThan(42));
  const floorSize = attribute('_kit_floors', 'vec3');
  const unitHeight = isPatch.select(size.y, mix(floorSize.x, floorSize.y, upper));
  const windowWidth = isPatch.select(float(1), special.select(float(.49), opening.x));
  const windowHeight = isPatch.select(float(1), special.select(float(.76), opening.y));
  const bottom = isPatch.select(float(0), special.select(float(0), opening.z));
  const half = windowWidth.mul(isPatch.select(size.x, layout.y)).div(2);
  const rise = min(half.mul(shape.equal(3).select(float(Math.sqrt(3)), shape.equal(2).select(float(1), float(.36)))), windowHeight.mul(unitHeight).mul(.5));
  const radius = half.mul(half).add(rise.mul(rise)).div(max(rise.mul(2), .001));
  const across = x.sub(.5).mul(isPatch.select(size.x, layout.y)).abs();
  const roundDrop = radius.sub(sqrt(max(radius.mul(radius).sub(min(across, half).pow(2)), 0)));
  // Two intersecting circular arcs meet at the apex of a pointed (Gothic) head.
  const pointedCap = sqrt(max(float(4).sub(across.div(max(half, .001)).clamp(0, 1).add(1).pow(2)), 0)).div(Math.sqrt(3));
  const drop = shape.equal(3).select(rise.mul(float(1).sub(pointedCap)), roundDrop).div(max(unitHeight, .001));
  const top = bottom.add(windowHeight).sub(shape.greaterThan(0).select(drop, float(0)));
  const py = metres.y.div(size.y).clamp(0, 1);
  const openingY = isPatch.select(py, y);
  const cutout = shape.greaterThan(0).and(across.lessThanEqual(half)).and(openingY.greaterThan(top));
  const module = cutout.select(blank, selectedModule);
  const horizontal = warp(x, float(.5).sub(opening.x.div(2)), float(.5).add(opening.x.div(2)),
    float(.5).sub(sourceWidth.div(2)), float(.5).add(sourceWidth.div(2)));
  const vertical = warp(y, opening.z, top, sourceSill, sourceSill.add(sourceHeight));
  // Doors and shopfronts keep their own complete atlas module at ground level.
  const ordinary = special.or(module.greaterThanEqual(42)).select(vec2(fract(bays.x), shape.greaterThan(0).and(special).select(y.div(max(top, .001)).mul(.76), y)), vec2(horizontal, vertical));
  const patchY = py.div(max(top, .001)).clamp(0, 1);
  const patch = patchKind.equal(2).select(vec2(x.sub(.5).mul(.49).add(.5), patchY.mul(.76)),
    vec2(x.sub(.5).mul(.45).add(.5), patchY.mul(.5).add(.21)));
  const local = cutout.select(vec2(.05, .95), isPatch.select(patch, ordinary));
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
