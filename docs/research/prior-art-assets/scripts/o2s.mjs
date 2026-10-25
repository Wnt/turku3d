import fs from "fs";
import {initSync, JsStreetNetwork} from "./node_modules/osm2streets-js/osm2streets_js.js";
initSync(fs.readFileSync(new URL("./node_modules/osm2streets-js/osm2streets_js_bg.wasm", import.meta.url)));
const xml = fs.readFileSync(process.argv[2], "utf8");
const clip = JSON.stringify({type:"Feature",properties:{},geometry:{type:"Polygon",coordinates:[[[22.2641,60.4505],[22.2731,60.4505],[22.2731,60.4551],[22.2641,60.4551],[22.2641,60.4505]]]}});
const opts = {osm2lanes:false, debug_each_step:false, dual_carriageway_experiment:false, sidepath_zipping_experiment:false, inferred_sidewalks:true, inferred_kerbs:true, date_time:undefined, override_driving_side:"Right"};
const t0=Date.now(); const n = new JsStreetNetwork(xml, clip, opts);
const lanes = JSON.parse(n.toLanePolygonsGeojson()); const marks=JSON.parse(n.toLaneMarkingsGeojson()); const inter=JSON.parse(n.toGeojsonPlain());
const c={}; for (const f of lanes.features) { const t=f.properties.type; c[t]=(c[t]||0)+1; }
console.log("ms",Date.now()-t0,"lane polygons",lanes.features.length,c,"markings",marks.features.length,"plain",inter.features.length);
console.log(JSON.stringify(lanes.features.find(f=>f.properties.type==="Sidewalk")?.properties));
fs.writeFileSync(process.argv[3], JSON.stringify(lanes)); fs.writeFileSync(process.argv[3].replace(".geojson","_plain.geojson"), JSON.stringify(inter));
