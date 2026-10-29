import type * as THREE from 'three/webgpu';

type Attribute = THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
interface Draw {
  object: THREE.Object3D;
  geometry: THREE.BufferGeometry;
  getAttributes(): Attribute[];
}
interface Owner { buffers: Set<object>; release: () => void }

/** r186 geometry cleanup sees only its first draw's node attributes. Other passes can add
 * instance/previous-instance fields. Own all of those until object/geometry retirement,
 * sharing by the actual interleaved buffer so one attribute view cannot destroy another. */
export class NodeAttributes {
  private seen = new WeakMap<Draw, Attribute[]>();
  private owners = new WeakMap<THREE.Object3D, Map<THREE.BufferGeometry, Owner>>();
  private buffers = new Map<object, { owners: Set<Owner>; attributes: Set<Attribute> }>();
  constructor(private remove: (attribute: Attribute) => void) {}

  dispose() {
    const owners = new Set([...this.buffers.values()].flatMap(record => [...record.owners]));
    for (const owner of owners) owner.release();
  }

  track(draw: Draw) {
    const attributes = draw.getAttributes();
    if (this.seen.get(draw) === attributes) return;
    this.seen.set(draw, attributes);
    const geometryBuffers = new Set(Object.values(draw.geometry.attributes).map(bufferOf));
    const generated = attributes.filter(attribute => !geometryBuffers.has(bufferOf(attribute)));
    if (!generated.length) return;
    let owners = this.owners.get(draw.object);
    if (!owners) { owners = new Map(); this.owners.set(draw.object, owners); }
    let owner = owners.get(draw.geometry);
    if (!owner) {
      const entry: Owner = { buffers: new Set(), release: () => {
        draw.object.removeEventListener('dispose' as never, entry.release);
        draw.geometry.removeEventListener('dispose', entry.release);
        owners!.delete(draw.geometry);
        for (const buffer of entry.buffers) {
          const record = this.buffers.get(buffer)!;
          record.owners.delete(entry);
          if (record.owners.size) continue;
          for (const attribute of record.attributes) this.remove(attribute);
          this.buffers.delete(buffer);
        }
        entry.buffers.clear();
      } };
      owner = entry; owners.set(draw.geometry, owner);
      draw.object.addEventListener('dispose' as never, owner.release);
      draw.geometry.addEventListener('dispose', owner.release);
    }
    for (const attribute of generated) {
      const buffer = bufferOf(attribute);
      let record = this.buffers.get(buffer);
      if (!record) { record = { owners: new Set(), attributes: new Set() }; this.buffers.set(buffer, record); }
      record.owners.add(owner); record.attributes.add(attribute); owner.buffers.add(buffer);
    }
  }
}
function bufferOf(attribute: Attribute): object {
  return (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
    ? (attribute as THREE.InterleavedBufferAttribute).data : attribute;
}

/** Install after renderer initialization, when its geometry/attribute managers exist. */
export function ownNodeAttributes(renderer: THREE.WebGPURenderer) {
  const internal = renderer as unknown as {
    _attributes: { delete(attribute: Attribute): void };
    _geometries: { updateForRender(draw: Draw): void };
  };
  const ownership = new NodeAttributes(attribute => internal._attributes.delete(attribute));
  const geometries = internal._geometries, update = geometries.updateForRender;
  geometries.updateForRender = function(draw) { update.call(this, draw); ownership.track(draw); };
  return () => { ownership.dispose(); geometries.updateForRender = update; };
}
