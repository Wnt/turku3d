// Trailer capture server: this worktree's game against the deployed OCI tile/scenery host (read-only).
import {mergeConfig} from '../../game/node_modules/vite/dist/node/index.js';
import base from '../../game/vite.config.ts';
import path from 'node:path';
const upstream=process.env.TRAILER_UPSTREAM??'https://city.madekivi.fi';
const proxied=target=>({target,changeOrigin:true,secure:true});
export default mergeConfig(base,{root:path.resolve(import.meta.dirname,'../../game'),
 server:{host:'127.0.0.1',proxy:{'/coarse':proxied(upstream)}}});
