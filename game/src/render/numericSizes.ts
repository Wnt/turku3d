import { Source } from 'three/webgpu';

/** r186's Source.getSize writes an image's width and height as they are, and a depth texture's image (the canvas
 *  target's, a render target's before its first size) has neither: Vector3.set(undefined, undefined, 1). That one
 *  store generalizes Vector3's x and y fields from doubles to tagged values for the whole page, and from then on every
 *  Vector3 write boxes a new heap number: on city.lab (2026-11-05) 2.4 MB per 100,000 vector operations, part of the
 *  76-170 MB/s that kept GC at up to 45 % of a 60 s profile. Missing sizes read as 0 instead; nothing compares a
 *  texture size to undefined (depth buffers are recreated whenever the size differs, as before). */
type Sized = { set(x: number, y: number, z?: number): unknown };
// Textures hold a TextureSource (r186: Source extends it and exports no own getSize): patch the class that owns it.
let owner: object | null = Source.prototype;
while (owner && !Object.prototype.hasOwnProperty.call(owner, 'getSize')) owner = Object.getPrototypeOf(owner) as object | null;
const prototype = owner as { data: unknown; getSize(target: Sized): Sized };
const size = prototype.getSize;
prototype.getSize = function getSize(target: Sized) {
  const data = this.data as { width?: number; height?: number; depth?: number } | null;
  if (data === null || typeof data !== 'object' || (typeof HTMLVideoElement !== 'undefined' && data instanceof HTMLVideoElement)
    || (typeof VideoFrame !== 'undefined' && data instanceof VideoFrame)) return size.call(this, target);
  target.set(data.width ?? 0, data.height ?? 0, data.depth || 0);
  return target;
};
