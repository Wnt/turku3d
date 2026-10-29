// Reuse the vendored game decoder: no process startup, PNG roundtrip or new dependency.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const directory = fileURLToPath(new URL('../../../game/public/basis/', import.meta.url));
const script = path.join(directory, 'basis_transcoder.js');
const source = fs.readFileSync(script, 'utf8');
const binary = fs.readFileSync(path.join(directory, 'basis_transcoder.wasm'));
export const facadeDecoderIdentity = createHash('sha256').update(source).update(binary).digest('hex');
let decoder;

function initialize() {
  // The vendored decoder is UMD, while this repository uses ESM. Give this trusted
  // local source its normal CommonJS bindings without copying or modifying it.
  const module = { exports:{} };
  new Function('module', 'exports', 'require', '__filename', '__dirname', source)(
    module, module.exports, createRequire(import.meta.url), script, directory);
  return module.exports({ wasmBinary:binary }).then(instance => {
    instance.initializeBasis();
    return instance;
  });
}

export async function decodeFacadeRgb(bytes) {
  const basis = await (decoder ??= initialize());
  const texture = new basis.KTX2File(new Uint8Array(bytes));
  try {
    if (!texture.isValid() || texture.getFaces() !== 1 || texture.getLayers() > 1 || texture.isHDR()) {
      throw new Error('Facade must be a supported two-dimensional Basis texture');
    }
    const width = texture.getWidth(), height = texture.getHeight();
    if (!width || !height || !texture.startTranscoding()) throw new Error('Facade transcoding failed');
    const rgba = new Uint8Array(texture.getImageTranscodedSizeInBytes(0, 0, 0, 13));
    if (rgba.length !== width * height * 4 || !texture.transcodeImage(rgba, 0, 0, 0, 13, 0, -1, -1)) {
      throw new Error('Facade RGB decoding failed');
    }
    // Match ktx extract --transcode rgb8: discard alpha before any resampling.
    const data = Buffer.allocUnsafe(width * height * 3);
    for (let input = 0, output = 0; input < rgba.length; input += 4) {
      data[output++] = rgba[input];
      data[output++] = rgba[input + 1];
      data[output++] = rgba[input + 2];
    }
    return { data, width, height };
  } finally {
    texture.close();
    texture.delete();
  }
}
