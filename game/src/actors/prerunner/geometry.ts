import * as T from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const v = (x: number, y: number, z: number) => new T.Vector3(x, y, z);
const up = v(0, 1, 0);
export function box(x: number, y: number, z: number, sx: number, sy: number, sz: number, ry = 0) {
  return new T.BoxGeometry(sx, sy, sz).rotateY(ry).translate(x, y, z);
}
export function tube(a: T.Vector3, b: T.Vector3, radius = .035, segments = 10) {
  const g = new T.CylinderGeometry(radius, radius, a.distanceTo(b), segments);
  g.applyQuaternion(new T.Quaternion().setFromUnitVectors(up, b.clone().sub(a).normalize()));
  return g.translate((a.x+b.x)/2, (a.y+b.y)/2, (a.z+b.z)/2);
}
export function panel(points: number[][], thickness = .018) {
  // Authored clockwise when viewed from outside; duplicate back faces for an actual thin sheet.
  const shape = new T.Shape(points.map(p => new T.Vector2(p[0], p[1])));
  return new T.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false });
}
export class Parts {
  private groups = new Map<T.Material, T.BufferGeometry[]>();
  add(material: T.Material, ...geometries: T.BufferGeometry[]) {
    let list = this.groups.get(material); if (!list) this.groups.set(material, list = []);
    list.push(...geometries); return this;
  }
  build(name: string, distant?: T.Material) {
    if (distant) {
      const all: T.BufferGeometry[] = [];
      for (const [material, geometries] of this.groups) {
        const color = material.userData.distantColor === undefined ? (material as T.MeshStandardMaterial).color : new T.Color(material.userData.distantColor);
        for (const geometry of geometries) {
          const values = new Float32Array(geometry.attributes.position.count * 3);
          for (let i = 0; i < values.length; i += 3) { values[i] = color.r; values[i+1] = color.g; values[i+2] = color.b; }
          geometry.setAttribute('color', new T.BufferAttribute(values,3)); all.push(geometry);
        }
      }
      this.groups.clear(); this.groups.set(distant, all);
    }
    const root = new T.Group(); root.name = name;
    for (const [material, geometries] of this.groups) {
      const nonIndexed = geometries.map(g => g.index ? g.toNonIndexed() : g);
      const geometry = mergeGeometries(nonIndexed, false)!;
      geometry.computeBoundingSphere();
      const mesh = new T.Mesh(geometry, material); mesh.name = `${name}/${material.name}`;
      mesh.castShadow = true; mesh.receiveShadow = true; root.add(mesh);
      for (const g of new Set([...geometries, ...nonIndexed])) g.dispose();
    }
    return root;
  }
}
/** Open, swept fender brow; its inner envelope clears the steered tyre at full compression. */
export function fender(x: number, z: number, width: number, radius = .75) {
  const positions:number[]=[], indices:number[]=[];
  const steps=12;
  for(let i=0;i<=steps;i++){
    const a=(.12+i/steps*.76)*Math.PI;
    for(const edge of [-1,1])positions.push(x+Math.cos(a)*radius,-.43+Math.sin(a)*radius,z+edge*width/2);
  }
  for(let i=0;i<steps;i++){const a=i*2;indices.push(a,a+1,a+2,a+1,a+3,a+2);}
  const lipStart=positions.length/3,inner=z-Math.sign(z)*width/2;
  for(let i=0;i<=steps;i++){
    const a=(.12+i/steps*.76)*Math.PI,xp=x+Math.cos(a)*radius,yp=-.43+Math.sin(a)*radius;
    positions.push(xp,yp,inner,xp,yp-.045,inner);
  }
  for(let i=0;i<steps;i++){const a=lipStart+i*2;indices.push(a,a+1,a+2,a+1,a+3,a+2);}
  const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(positions,3));
  g.setAttribute('uv',new T.Float32BufferAttribute(Array.from({length:(steps+1)*4},(_,i)=>[Math.floor(i/2)/steps,i%2]).flat(),2));
  g.setIndex(indices);g.computeVertexNormals();return g;
}
export function strap(a:T.Vector3,b:T.Vector3,width=.045){
  return new T.BoxGeometry(width,a.distanceTo(b),.012).applyQuaternion(new T.Quaternion().setFromUnitVectors(up,b.clone().sub(a).normalize())).translate((a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2);
}
/** Original hoses, primaries and ducts; immutable curves are built once and merged by finish. */
export function sweptTube(points:number[][],radius:number,radialSegments=6){
  return new T.TubeGeometry(new T.CatmullRomCurve3(points.map(p=>v(p[0],p[1],p[2]))),Math.max(8,(points.length-1)*6),radius,radialSegments,false);
}
/** Mirror thickness while retaining the longitudinal panel outline and readable outward artwork. */
export function reflectPanelZ(g:T.BufferGeometry){
  g.scale(1,1,-1);
  const uv=g.getAttribute('uv');for(let i=0;i<uv.count;i++)uv.setX(i,1-uv.getX(i));
  if(g.index){for(let i=0;i<g.index.count;i+=3){const b=g.index.getX(i+1);g.index.setX(i+1,g.index.getX(i+2));g.index.setX(i+2,b);}}
  else for(const attribute of Object.values(g.attributes))for(let i=0;i<attribute.count;i+=3)for(let j=0;j<attribute.itemSize;j++){
    const b=attribute.getComponent(i+1,j);attribute.setComponent(i+1,j,attribute.getComponent(i+2,j));attribute.setComponent(i+2,j,b);
  }
  g.computeVertexNormals();return g;
}
