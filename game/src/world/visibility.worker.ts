import { computeOcclusion, DepthGrid } from './occlusionRaster';
import { layout, CAMERA, CONTROL } from './visibilityLayout';

/** Waits for each camera the main thread publishes, rasterises occluders and writes the occluded-slot bitmask
 * into the result half matching the camera sequence's parity. The main thread reads it one frame later. */
self.onmessage = ({ data }: MessageEvent<{ buffer: SharedArrayBuffer }>) => {
  const views = layout(data.buffer), grid = new DepthGrid();
  const vp = new Float64Array(16), eye = new Float64Array(3);
  let last = 0;
  for (;;) {
    Atomics.wait(views.control, CONTROL.cameraSeq, last);
    // Seqlock: the camera block is stable when the sequence is unchanged across the copy.
    let seq = Atomics.load(views.control, CONTROL.cameraSeq), near = 0;
    for (;;) {
      vp.set(views.camera.subarray(CAMERA.vp, CAMERA.vp + 16)); eye.set(views.camera.subarray(CAMERA.eye, CAMERA.eye + 3));
      near = views.camera[CAMERA.near];
      const again = Atomics.load(views.control, CONTROL.cameraSeq);
      if (again === seq) break;
      seq = again;
    }
    last = seq;
    const half = seq & 1, slotCount = Atomics.load(views.control, CONTROL.slotCount);
    const started = performance.now();
    const hidden = computeOcclusion({ slots: views.slots, boxCounts: views.boxCounts, boxes: views.boxes, slotCount },
      vp, eye, near, grid, views.results[half]);
    views.resultEye[half].set(eye);
    views.resultEye[half][3] = hidden; views.resultEye[half][4] = performance.now() - started;
    Atomics.store(views.control, CONTROL.resultSeq, seq);
  }
};
