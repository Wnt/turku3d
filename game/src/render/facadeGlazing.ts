// Measured opening rectangles are not single scalable bitmaps. Keep frame and
// mullion widths in metres, including long ribbon glazing and tall church lights.
import { float, fract, fwidth, max, min, round, smoothstep, step, vec4 } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';

export function metricGlazing(metres: Node<'vec2'>, size: Node<'vec2'>, top: Node<'float'>,
  pattern: Node<'vec4'> = vec4(0, 0, 0, -1), phase: Node<'float'> = float(0)) {
  const columns = pattern.x.lessThan(0).select(max(2, size.x.div(max(.01, pattern.x.negate())).ceil()),
    pattern.x.greaterThan(0).select(pattern.x,
      max(size.x.greaterThan(.8).select(float(2), float(1)), round(size.x.div(1.25)))));
  const rows = pattern.y.greaterThan(0).select(pattern.y, max(1, round(size.y.div(1.8))));
  const pitchX = pattern.x.lessThan(0).select(pattern.x.negate(), size.x.div(columns)), pitchY = size.y.div(rows);
  const x = metres.x.div(pitchX).add(phase), y = metres.y.div(pitchY);
  const edge = min(min(metres.x, size.x.sub(metres.x)), min(metres.y, top.mul(size.y).sub(metres.y)));
  const aa = max(.002, max(fwidth(metres.x), fwidth(metres.y)));
  const frameWidth = min(.065, size.x.mul(.12));
  const frame = float(1).sub(smoothstep(frameWidth.sub(aa), frameWidth.add(aa), edge));
  const dx = min(fract(x), float(1).sub(fract(x))).mul(pitchX);
  const dy = min(fract(y), float(1).sub(fract(y))).mul(pitchY);
  const vertical = float(1).sub(smoothstep(float(.022).sub(aa), float(.022).add(aa), dx)).mul(step(1.5, columns));
  const horizontal = float(1).sub(smoothstep(float(.022).sub(aa), float(.022).add(aa), dy)).mul(step(1.5, rows));
  const split = float(1).sub(smoothstep(float(.022).sub(aa), float(.022).add(aa),
    fract(x).sub(pattern.z).abs().mul(pitchX))).mul(float(pattern.z.greaterThan(0)));
  const transomHeight = pattern.w.lessThan(0).select(float(.68), pattern.w);
  const transom = float(1).sub(smoothstep(float(.022).sub(aa), float(.022).add(aa),
    metres.y.sub(size.y.mul(transomHeight)).abs())).mul(step(1.1, size.y))
    .mul(float(rows.lessThan(1.5))).mul(float(transomHeight.greaterThan(0)));
  const inside = smoothstep(aa.negate(), aa, edge);
  const glass = inside.mul(float(1).sub(max(frame, max(vertical, max(split, max(horizontal, transom))))));
  return { glass, inside, column: x.floor(), rise: metres.y.div(size.y).clamp(0, 1) };
}
