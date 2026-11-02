import * as THREE from 'three/webgpu';
import type { TypedArray } from 'three/src/core/BufferAttribute.js';

type Attribute = THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
function layout(attribute: Attribute) {
  const interleaved = attribute as THREE.InterleavedBufferAttribute;
  const array = attribute.array;
  let scale = 1, minimum = -Infinity;
  if (attribute.normalized) {
    if (array instanceof Uint8Array || array instanceof Uint8ClampedArray) scale = 1 / 255;
    else if (array instanceof Uint16Array) scale = 1 / 65535;
    else if (array instanceof Uint32Array) scale = 1 / 4294967295;
    else if (array instanceof Int8Array) { scale = 1 / 127; minimum = -1; }
    else if (array instanceof Int16Array) { scale = 1 / 32767; minimum = -1; }
    else if (array instanceof Int32Array) { scale = 1 / 2147483647; minimum = -1; }
  }
  return { array, scale, minimum, stride: interleaved.isInterleavedBufferAttribute ? interleaved.data.stride : attribute.itemSize,
    offset: interleaved.isInterleavedBufferAttribute ? interleaved.offset : 0 };
}

function copyAttribute(source: Attribute, destination: Attribute, vertexOffset: number) {
  const a = layout(source), b = layout(destination), size = source.itemSize;
  const raw = source.normalized === destination.normalized && a.array.constructor === b.array.constructor;
  if (raw && a.stride === size && b.stride === size) {
    b.array.set(a.array, vertexOffset * size);
  } else if (raw) {
    for (let i=0, src=a.offset, dst=vertexOffset*b.stride+b.offset; i<source.count; i++,src+=a.stride,dst+=b.stride)
      for (let c=0;c<size;c++) b.array[dst+c] = a.array[src+c];
  } else {
    // Only normalized/strided inputs that need conversion reach this path.
    // Destination conversion buffers are floats; packed facade inputs normally use one bulk copy below.
    for (let i=0, src=a.offset, dst=vertexOffset*b.stride+b.offset; i<source.count; i++,src+=a.stride,dst+=b.stride)
      for (let c=0;c<size;c++) b.array[dst+c] = Math.max(a.minimum, a.array[src+c] * a.scale);
  }
}

/** Bake compatible scenery into final buffers. Bulk-copy packed facade instructions,
 * colours and UVs; direct numeric loops handle only transforms, conversion and index rebasing. */
export function mergeSceneryGeometry(meshes: readonly THREE.Mesh[]): THREE.BufferGeometry {
  const first = meshes[0].geometry;
  const vertices = meshes.reduce((sum, mesh) => sum + mesh.geometry.attributes.position.count, 0);
  const geometry = new THREE.BufferGeometry();
  const fields = first.hasAttribute('_kit_opening')
    ? Object.keys(first.attributes).filter(name => name.startsWith('_kit_')).sort() : [];
  const fieldSet = new Set(fields);
  let program: THREE.InterleavedBuffer | undefined;
  if (fields.length) {
    const stride = fields.reduce((sum, name) => sum + first.attributes[name].itemSize, 0);
    program = new THREE.InterleavedBuffer(new Float32Array(vertices * stride), stride);
    let offset = 0;
    for (const name of fields) {
      const size = first.attributes[name].itemSize;
      geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(program, size, offset));
      offset += size;
    }
  }
  for (const [name, attribute] of Object.entries(first.attributes)) {
    if (geometry.hasAttribute(name)) continue;
    const floats = ['position', 'normal', 'tangent'].includes(name)
      || (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute;
    const ArrayType = floats ? Float32Array : attribute.array.constructor as {new(length: number): TypedArray};
    geometry.setAttribute(name, new THREE.BufferAttribute(new ArrayType(vertices * attribute.itemSize),
      attribute.itemSize, floats ? false : attribute.normalized));
  }
  if (first.index) {
    const count = meshes.reduce((sum, mesh) => sum + mesh.geometry.index!.count, 0);
    geometry.setIndex(new THREE.BufferAttribute(vertices > 65535 ? new Uint32Array(count) : new Uint16Array(count), 1));
  }
  const normalMatrix = new THREE.Matrix3();
  let vertexOffset = 0, indexOffset = 0;
  const attributes = Object.entries(geometry.attributes);
  for (const mesh of meshes) {
    const source = mesh.geometry, matrix = mesh.matrixWorld.elements;
    normalMatrix.getNormalMatrix(mesh.matrixWorld);
    const packed = source.attributes[fields[0]] as THREE.InterleavedBufferAttribute | undefined;
    const copyProgram = !!program && !!packed?.isInterleavedBufferAttribute
      && packed.data.array instanceof Float32Array && packed.data.stride === program.stride
      && fields.every(name => {
        const a = source.attributes[name] as THREE.InterleavedBufferAttribute;
        return a.data === packed.data && !a.normalized
          && a.offset === (geometry.attributes[name] as THREE.InterleavedBufferAttribute).offset;
      });
    if (copyProgram) program!.array.set(packed!.data.array, vertexOffset * program!.stride);
    for (const [name, destination] of attributes) {
      if (copyProgram && fieldSet.has(name)) continue;
      const attribute = source.attributes[name];
      if (name !== 'position' && name !== 'normal' && name !== 'tangent') {
        copyAttribute(attribute, destination, vertexOffset); continue;
      }
      const a = layout(attribute), output = destination.array, size = attribute.itemSize;
      if (name === 'position') {
        const affine = matrix[3] === 0 && matrix[7] === 0 && matrix[11] === 0 && matrix[15] === 1;
        for (let i=0, src=a.offset, dst=vertexOffset*size; i<attribute.count; i++,src+=a.stride,dst+=size) {
          const x=Math.max(a.minimum,a.array[src]*a.scale), y=Math.max(a.minimum,a.array[src+1]*a.scale), z=Math.max(a.minimum,a.array[src+2]*a.scale);
          const w=affine ? 1 : 1/(matrix[3]*x+matrix[7]*y+matrix[11]*z+matrix[15]);
          output[dst]=(matrix[0]*x+matrix[4]*y+matrix[8]*z+matrix[12])*w;
          output[dst+1]=(matrix[1]*x+matrix[5]*y+matrix[9]*z+matrix[13])*w;
          output[dst+2]=(matrix[2]*x+matrix[6]*y+matrix[10]*z+matrix[14])*w;
        }
      } else {
        const m=name === 'normal' ? normalMatrix.elements : matrix, step=name === 'normal' ? 3 : 4;
        const m0=m[0],m1=m[1],m2=m[2],m3=m[step],m4=m[step+1],m5=m[step+2],m6=m[2*step],m7=m[2*step+1],m8=m[2*step+2];
        for (let i=0, src=a.offset, dst=vertexOffset*size; i<attribute.count; i++,src+=a.stride,dst+=size) {
          const x=Math.max(a.minimum,a.array[src]*a.scale), y=Math.max(a.minimum,a.array[src+1]*a.scale), z=Math.max(a.minimum,a.array[src+2]*a.scale);
          const nx=m0*x+m3*y+m6*z, ny=m1*x+m4*y+m7*z, nz=m2*x+m5*y+m8*z;
          const inverseLength=1/(Math.sqrt(nx*nx+ny*ny+nz*nz)||1);
          output[dst]=nx*inverseLength; output[dst+1]=ny*inverseLength; output[dst+2]=nz*inverseLength;
          if (size === 4) output[dst+3]=Math.max(a.minimum,a.array[src+3]*a.scale);
        }
      }
    }
    if (source.index) {
      const input=source.index.array, output=geometry.index!.array;
      if (!vertexOffset) output.set(input,indexOffset);
      else for (let i=0;i<input.length;i++) output[indexOffset+i]=input[i]+vertexOffset;
      indexOffset+=input.length;
    }
    vertexOffset += source.attributes.position.count;
  }
  // Three's generic bounds helpers use getX/getY/getZ per vertex. The output is
  // a tightly packed Float32 position array, so compute the same bounds directly.
  const positions=geometry.attributes.position.array;
  let minX=Infinity,minY=Infinity,minZ=Infinity,maxX=-Infinity,maxY=-Infinity,maxZ=-Infinity;
  for (let i=0;i<positions.length;i+=3) {
    const x=positions[i],y=positions[i+1],z=positions[i+2];
    minX=Math.min(minX,x); minY=Math.min(minY,y); minZ=Math.min(minZ,z);
    maxX=Math.max(maxX,x); maxY=Math.max(maxY,y); maxZ=Math.max(maxZ,z);
  }
  geometry.boundingBox=new THREE.Box3(new THREE.Vector3(minX,minY,minZ),new THREE.Vector3(maxX,maxY,maxZ));
  const centre=geometry.boundingBox.getCenter(new THREE.Vector3());
  let radiusSq=0;
  for (let i=0;i<positions.length;i+=3) {
    const x=positions[i]-centre.x,y=positions[i+1]-centre.y,z=positions[i+2]-centre.z;
    radiusSq=Math.max(radiusSq,x*x+y*y+z*z);
  }
  geometry.boundingSphere=new THREE.Sphere(centre,Math.sqrt(radiusSq));
  return geometry;
}
