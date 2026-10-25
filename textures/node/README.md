# node/
KTX2 encoder wrapper (ktx2-encoder 0.4.2 = basis_universal WASM; no native toktx needed).
`npm ci` (or `make textures-setup`), then `node encode.mjs in.png out.ktx2 etc1s|uastc|uastc-normal [--linear] [--quality 128]`.
Albedo: ETC1S sRGB. ORM: ETC1S `--linear`. Normals: `uastc-normal` (linear, zstd + RDO). All with mipmaps.
