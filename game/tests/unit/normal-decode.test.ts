import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { reflect, vec3, vec4 } from 'three/tsl';
import { decodePrepassNormal } from '../../src/render/normalDecode';

test('receiver alpha never turns a normal or reflected ray into a four-dimensional vector', () => {
  // The base JS constructor supplies IR caches; backend code-generation methods are unused here.
  const builder = Reflect.construct(THREE.NodeBuilder, [null, null, null]) as THREE.NodeBuilder;
  for (const receiver of [0, 1]) {
    const normal = decodePrepassNormal(vec4(.5, 1, .5, receiver));
    expect(normal.getNodeType(builder)).toBe('vec3');
    expect(reflect(vec3(0, -1, -1), normal).getNodeType(builder)).toBe('vec3');
  }
});
