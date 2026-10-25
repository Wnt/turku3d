"""Render actual layer GLBs and a model catalogue in isolated desktop Chrome."""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

from turku3d import config as C
from turku3d.layers import layer_root, read_manifest
from turku3d.package.manifest import _atomic_write
from turku3d.props import Prop, visual_bytes
from turku3d.props.models import SUPPORTED_SIGNS


def main():
    root = layer_root() / "props"
    manifest = read_manifest(root)
    instances = json.loads((root / "instances.json").read_text())
    office = C.en_to_local(C.OFFICE_E, C.OFFICE_N)
    focus = min(
        (p for p in instances if p["kind"] == "bench"),
        key=lambda p: (p["x"] - office[0]) ** 2 + (p["z"] - office[2]) ** 2,
    )
    focus_sign = min(
        (p for p in instances if p["kind"] == "sign"),
        key=lambda p: (p["x"] - 290) ** 2 + (p["z"] - 197) ** 2,
    )
    out = C.work_root() / "reports/props"
    out.mkdir(parents=True, exist_ok=True)
    catalogue = [
        Prop(f"catalog:{kind}", kind, i * 3, 0, 0)
        for i, kind in enumerate(
            [
                "bench",
                "bin",
                "bollard",
                "bike_rack",
                "bike_station",
                "parking_meter",
                "lamp",
                "bus_stop",
                "post",
            ]
        )
    ]
    catalogue.extend(
        Prop(f"catalog:{code}", "sign", i * 3, 0, -5, code=code)
        for i, code in enumerate(sorted(SUPPORTED_SIGNS))
    )
    _atomic_write(out / "catalog.glb", visual_bytes(catalogue, C.VisualTile(0, 0, 0)))
    _atomic_write(
        out / "scene.json",
        json.dumps(
            {
                "tiles": [n for n in manifest["tiles"] if n.endswith(".glb")],
                "context": {
                    layer: [
                        n
                        for n in read_manifest(layer_root() / layer)["tiles"]
                        if n.endswith(".glb")
                    ]
                    for layer in ("ground", "buildings")
                    if (layer_root() / layer / "MANIFEST.json").exists()
                },
                "metrics": manifest["params"]["metrics"],
                "focus": focus,
                "focus_sign": focus_sign,
            }
        ).encode(),
    )
    _atomic_write(out / "index.html", Path(__file__).with_name("preview.html").read_bytes())

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            clean = path.split("?")[0]
            if ".." in clean.split("/"):
                return str(out / "missing")
            if clean.startswith("/three/"):
                return str(C.REPO_ROOT / "game/node_modules/three" / clean.removeprefix("/three/"))
            if clean.startswith("/layer/"):
                name = clean.removeprefix("/layer/")
                return str((out if name == "catalog.glb" else root) / name)
            if clean.startswith("/context/"):
                return str(layer_root() / clean.removeprefix("/context/"))
            return super().translate_path(path)

        def log_message(self, *args):
            pass

    port = int(os.environ["TURKU3D_PORT_BASE"]) + 3
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), functools.partial(Handler, directory=str(out))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = r"""
const {chromium}=require(process.argv[1]);
(async()=>{
const context=await chromium.launchPersistentContext(process.argv[2],{channel:'chrome',headless:true,viewport:{width:1600,height:1000},args:['--use-angle=metal','--ignore-gpu-blocklist']});
try {
 const results=[];
 for(const mode of ['aoi','catalog','detail','street','sign']){
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.argv[3]+(mode==='aoi'?'':'?'+mode));
  await page.waitForFunction(()=>window.__propsReady,{},{timeout:60000});
  await page.screenshot({path:process.argv[4]+'/'+mode+'.png'});
  results.push({mode,errors,...await page.evaluate(()=>window.__propsStats)});
  await page.close();if(errors.length)throw Error(errors.join(';'));
 }
 require('fs').writeFileSync(process.argv[4]+'/browser.json',JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
}finally{await context.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    try:
        subprocess.run(
            [
                "node",
                "-e",
                script,
                str(C.REPO_ROOT / "game/node_modules/@playwright/test"),
                str(Path(os.environ["TURKU3D_BROWSER_DIR"]) / "props"),
                f"http://127.0.0.1:{port}/",
                str(out),
            ],
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()
    print(out)


if __name__ == "__main__":
    main()
