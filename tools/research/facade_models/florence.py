"""Florence-2 ablation; run in the pinned Transformers 4.49 environment."""

import os
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor
from turkutex import facade_cv
from turkutex.facade_cv_benchmark import benchmark
from turkutex.facade_models import MODELS, ROOT, Engine

torch.set_num_threads(2)
d = ROOT / "florence"
assert (d / "revision.txt").read_text().strip() == MODELS["florence"][1]
start = time.monotonic()
processor = AutoProcessor.from_pretrained(d, local_files_only=True, trust_remote_code=True)
model = (
    AutoModelForCausalLM.from_pretrained(
        d,
        local_files_only=True,
        trust_remote_code=True,
        torch_dtype=torch.float16,
        attn_implementation="eager",
    )
    .eval()
    .to("mps")
)
print("loaded", time.monotonic() - start, flush=True)


class Florence:
    name = "florence"
    masks = Engine.masks

    def measure(self, rgb, fid, width, height):
        prompt = "<CAPTION_TO_PHRASE_GROUNDING>window. door. shop window. balcony."
        inputs = processor(text=prompt, images=Image.fromarray(rgb), return_tensors="pt").to(
            "mps", torch.float16
        )
        with torch.inference_mode():
            ids = model.generate(**inputs, max_new_tokens=512, do_sample=False, num_beams=1)
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task="<CAPTION_TO_PHRASE_GROUNDING>", image_size=(rgb.shape[1], rgb.shape[0])
        )
        data = parsed["<CAPTION_TO_PHRASE_GROUNDING>"]
        self.boxes = [
            dict(box=box, label=label, score=1)
            for box, label in zip(data["bboxes"], data["labels"], strict=True)
        ]
        answer, sidecar = facade_cv.measure(rgb, fid, width, height, self)
        sidecar.update(boxes=self.boxes, engine="florence", raw_text=text)
        print(fid, len(self.boxes), flush=True)
        return answer, sidecar


benchmark(
    Path.home() / "turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure",
    Path(os.environ["TURKU3D_DATA"]) / "facade-approvals/assembly",
    Path(os.environ["TURKU3D_WORK"]) / "reports/models/florence.json",
    "classic",
    measure_fn=Florence().measure,
    engine="florence",
)
