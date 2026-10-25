"""Capture actual before/after layer geometry at fixed riverside camera poses."""

import functools
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

from turku3d import config as C
from turku3d.layers import layer_root, read_manifest


def main():
    out = C.work_root() / "reports/placement"
    before = out / "before"
    current = layer_root()

    def urls(root, prefix, layers):
        return [
            f"/{prefix}/{layer}/{name}"
            for layer in layers
            for name in read_manifest(root / layer)["tiles"]
            if name.endswith(".glb")
        ]

    scene = {
        "context": urls(current, "current", ["ground", "buildings"]),
        "before": urls(before, "before", ["props", "vegetation", "traffic_static"]),
        "after": urls(current, "current", ["props", "vegetation", "traffic_static"]),
        "views": {
            "teatterisilta": {
                "label": "Teatterisilta: unsupported bridge benches",
                "eye": [25, 12, 420],
                "target": [6, 3, 395],
            },
            "aurasilta": {
                "label": "Aurasilta vicinity",
                "eye": [365, 45, 310],
                "target": [305, 3, 230],
            },
            "rantakatu": {
                "label": "Läntinen Rantakatu 15",
                "eye": [220, 25, 300],
                "target": [168, 6, 246],
            },
        },
    }
    (out / "scene.json").write_text(json.dumps(scene))
    (out / "preview.html").write_text(Path(__file__).with_name("preview.html").read_text())

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            clean = path.split("?")[0]
            if ".." in clean.split("/"):
                return str(out / "missing")
            for prefix, root in [
                ("/three/", C.REPO_ROOT / "game/node_modules/three"),
                ("/current/", current),
                ("/before/", before),
            ]:
                if clean.startswith(prefix):
                    return str(root / clean.removeprefix(prefix))
            return super().translate_path(path)

        def log_message(self, *args):
            pass

    port = int(os.environ["TURKU3D_PORT_BASE"]) + 3
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", port), functools.partial(Handler, directory=str(out))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    script = r"""
const {chromium}=require(process.argv[1]);
(async()=>{const context=await chromium.launchPersistentContext(process.argv[2],{
channel:'chrome',headless:true,viewport:{width:1400,height:950},args:['--use-angle=metal']});
try{for(const phase of ['before','after'])for(const view of ['aurasilta','rantakatu','teatterisilta']){
const page=await context.newPage();await page.goto(process.argv[3]+'/preview.html?phase='+phase+'&view='+view);
await page.waitForFunction(()=>window.__placementReady,{},{timeout:120000});
await page.screenshot({path:process.argv[4]+'/'+view+'-'+phase+'.png'});await page.close();
}}finally{await context.close();}})().catch(e=>{console.error(e);process.exitCode=1});
"""
    try:
        subprocess.run(
            [
                "node",
                "-e",
                script,
                str(C.REPO_ROOT / "game/node_modules/@playwright/test"),
                str(Path(os.environ["TURKU3D_BROWSER_DIR"]) / "placement"),
                f"http://127.0.0.1:{port}",
                str(out),
            ],
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
