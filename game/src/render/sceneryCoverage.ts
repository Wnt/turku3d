import * as THREE from 'three/webgpu';
import { sceneryBounds } from '../world/sceneryView';

/** 250 m coverage cells let the continuous regional terrain yield to real loaded surfaces. */
export class SceneryCoverage {
  readonly size = 256;
  readonly pixels = new Uint8Array(this.size * this.size);
  readonly texture = new THREE.DataTexture(this.pixels, this.size, this.size, THREE.RedFormat);
  private signature = '';
  constructor() {
    this.texture.minFilter = this.texture.magFilter = THREE.NearestFilter;
    this.texture.generateMipmaps = false;
    this.texture.needsUpdate = true;
  }
  update(ids: string[]) {
    const signature = [...new Set(ids)].sort().join(',');
    if (signature === this.signature) return;
    this.signature = signature; this.pixels.fill(0);
    for (const id of ids) {
      const b = sceneryBounds(id), x = b.x / 250 + this.size / 2, z = b.z / 250 + this.size / 2;
      for (let j = Math.max(0, z); j < Math.min(this.size, z + b.size / 250); j++)
        this.pixels.fill(255, j * this.size + Math.max(0, x), j * this.size + Math.min(this.size, x + b.size / 250));
    }
    this.texture.needsUpdate = true;
  }
}
