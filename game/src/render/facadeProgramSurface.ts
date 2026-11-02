// One opening layout and lighting identity for detailed and distant facade programs.
import { float, floor, mix, positionWorld, round, step, uv, vec2, vec4 } from 'three/tsl';
import { facadeAttribute, facadePaintAttributes } from './facadeProgramAttributes';
import { programLayout, programPigment } from './facadeProgram';
import { facadeKey, quantise, wallFrame, windowEmission } from './nightLights';

export function facadeProgramSurface(version = 5) {
  const layout = facadeAttribute('_kit_layout', 'vec3');
  const metres = uv().sub(facadeAttribute('_kit_extent', 'vec4').xy);
  const paint = facadePaintAttributes(), floors = paint.floors;
  const upper = step(floors.x, metres.y);
  const bays = vec2(metres.x.div(layout.y), mix(metres.y.div(floors.x), metres.y.sub(floors.x).div(floors.y).add(1), upper));
  const entrance = layout.x.lessThan(42).and(layout.x.mod(3).equal(1));
  const selected = entrance.and(floor(bays.x).notEqual(floors.z)).and(upper.lessThan(1)).select(layout.x.sub(1), layout.x);
  const frame = version >= 5 ? facadeAttribute('_kit_frame', 'vec4') : vec4(paint.trim, 0);
  const program = programLayout(bays, metres, layout, upper, selected,
    version >= 5 ? facadeAttribute('_kit_glazing', 'vec4') : undefined, frame.w, true);
  const pigment = programPigment(metres, bays, upper, layout, paint.wall, paint.trim, program);
  const wall = wallFrame();
  const identity = facadeKey([round(wall.along.sub(metres.x).mul(4)), round(positionWorld.y.sub(metres.y).mul(4)),
    quantise(layout.y, 4096), quantise(layout.z, 4096)]).bitXor(wall.plane);
  const glazing = program.glass.mul(float(1).sub(pigment.band)).mul(float(1).sub(pigment.balcony));
  const pane = float(layout.x.lessThan(42).and(layout.x.div(6).floor().equal(5)));
  return { program, pigment, frame, glazing,
    emission: windowEmission(identity, program.column, program.row, program.rise, glazing, pane) };
}
