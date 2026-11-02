import type { Node } from 'three/webgpu';
import { unpackRGBToNormal } from 'three/tsl';

/** r186 preserves the sampled texture's arity: discard alpha before vector arithmetic. */
export function decodePrepassNormal(value: Node<'vec4'>) { return unpackRGBToNormal(value.rgb); }
