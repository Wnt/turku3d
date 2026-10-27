"""Publisher-pinned local facade model experiments; inference is strictly offline."""

from __future__ import annotations

import json
from pathlib import Path
from string import Template

import numpy as np
from PIL import Image

from . import facade_cv
from .facade_fast_render import validate

ROOT = Path.home() / "turku3d/tools/cv/cvr-models"
MODELS = {
    "cmp": ("Marco333/segformer-b0-facade-cmp", "f95d623ab89d1179d9f59b8d390cc7a2818d255a"),
    "smolvlm": ("HuggingFaceTB/SmolVLM-500M-Instruct", "a7da5b986cb59b408707209984f360a5f4ad7e47"),
    "qwen": ("Qwen/Qwen2.5-VL-3B-Instruct", "66285546d2b821cf421d4f5eb2576359d3770cd3"),
    "grounding-dino": (
        "IDEA-Research/grounding-dino-tiny",
        "a2bb814dd30d776dcf7e30523b00659f4f141c71",
    ),
    "siglip": ("google/siglip-base-patch16-224", "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"),
    "florence": ("microsoft/Florence-2-base-ft", "f6c1a25888ffc1d945ee8a1a77ac833c7303d46e"),
}


def prepare(name: str):
    """Explicit publisher download, at most 10 GB in this task cache; never executes code."""
    import requests

    model, revision = MODELS[name]
    directory = ROOT / name
    directory.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        f"https://huggingface.co/api/models/{model}/tree/{revision}", timeout=30
    )
    response.raise_for_status()
    files = response.json()
    for item in files:
        filename = item["path"]
        extensions = (".json", ".safetensors", ".txt", ".model") + (
            (".py",) if name == "florence" else ()
        )
        if "/" in filename or not filename.endswith(extensions):
            continue
        target = directory / filename
        if target.exists() and target.stat().st_size == item["size"]:
            continue
        if (
            sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file()) + item["size"]
            > 10_000_000_000
        ):
            raise RuntimeError("10 GB task cache budget exceeded")
        url = f"https://huggingface.co/{model}/resolve/{revision}/{filename}"
        with requests.get(url, timeout=120, stream=True) as response:
            response.raise_for_status()
            partial = target.with_suffix(".partial")
            with partial.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
            if partial.stat().st_size != item["size"]:
                raise ValueError(f"incomplete model file: {filename}")
            partial.replace(target)
    (directory / "revision.txt").write_text(revision + "\n")
    print(
        json.dumps(
            dict(
                model=model,
                revision=revision,
                bytes=sum(p.stat().st_size for p in directory.iterdir()),
            )
        )
    )


def parse_answer(text: str, facade_id: str) -> dict:
    """Accept an optional markdown wrapper, never silently repair fields or invent success."""
    start = text.find("{")
    if start < 0:
        raise ValueError("model did not return a JSON object")
    answer, _ = json.JSONDecoder().raw_decode(text[start:])
    answer["facade_id"] = facade_id
    errors = validate(answer, facade_id)
    if errors:
        raise ValueError("; ".join(errors))
    return answer


def metric_grid(openings, width_m, height_m):
    """Count semantic opening peaks with minimum physical storey/bay separation."""
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import find_peaks

    def axis(mask, projection_axis, span_m, minimum_m):
        profile = mask.mean(axis=projection_axis)
        px_per_m = len(profile) / max(span_m, 0.1)
        profile = gaussian_filter1d(profile, max(1.0, px_per_m * 0.12))
        peaks, _ = find_peaks(
            profile,
            prominence=max(0.035, float(np.ptp(profile)) * 0.2),
            distance=max(1, px_per_m * minimum_m),
        )
        return len(peaks)

    rows = axis(openings, 1, height_m, 2.4)
    upper = openings[: max(1, round(openings.shape[0] * (1 - 1 / max(2, rows))))]
    columns = axis(upper, 0, width_m, 0.9) if rows > 1 else 0
    return dict(storeys=min(30, rows), window_columns=min(60, columns))


class Engine:
    def __init__(self, name: str, *, device="mps", short=False, size=512):
        import torch
        from transformers import (
            AutoModelForVision2Seq,
            AutoModelForZeroShotObjectDetection,
            AutoProcessor,
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )

        self.name, self.device, self.short, self.size = name, device, short, size
        self.variant = name
        if name == "cv-models":
            self.parser = Engine("cmp", device=device)
            self.detector = Engine("grounding-dino-openings", device=device, size=800)
            return
        if name == "cmp-grid":
            name = self.name = "cmp"
        if name == "grounding-dino-openings":
            name = self.name = "grounding-dino"
        self.torch = torch
        torch.set_num_threads(2)
        directory = ROOT / name
        if (directory / "revision.txt").read_text().strip() != MODELS[name][1]:
            raise ValueError("model revision mismatch; run prepare")
        if name == "cmp":
            self.processor = SegformerImageProcessor.from_pretrained(
                directory, local_files_only=True
            )
            self.model = (
                SegformerForSemanticSegmentation.from_pretrained(directory, local_files_only=True)
                .eval()
                .to(device)
            )
            return
        self.processor = AutoProcessor.from_pretrained(directory, local_files_only=True)
        if name == "grounding-dino":
            self.model = (
                AutoModelForZeroShotObjectDetection.from_pretrained(
                    directory, local_files_only=True
                )
                .eval()
                .to(device)
            )
        else:
            self.model = (
                AutoModelForVision2Seq.from_pretrained(
                    directory,
                    local_files_only=True,
                    torch_dtype=torch.float16,
                    attn_implementation="sdpa",
                )
                .eval()
                .to(device)
            )

    def measure(self, rgb, facade_id, width_m, height_m):
        if self.variant == "cv-models":
            answer, parser_meta = self.parser.measure(rgb, facade_id, width_m, height_m)
            counts, detector_meta = self.detector.measure(rgb, facade_id, width_m, height_m)
            answer["storeys"] = counts["storeys"]
            if answer["storeys"] <= 1:
                answer["window_columns"] = 0
                answer["window_width"] = answer["window_height"] = 0
            sidecar = dict(parser_meta)
            sidecar.update(
                engine="cv-models",
                components={"cmp": parser_meta, "grounding-dino": detector_meta},
                needs_review=True,
            )
            sidecar["confidence"] = dict(
                parser_meta["confidence"], storeys=detector_meta["confidence"]["storeys"]
            )
            return answer, sidecar
        if self.name == "cmp":
            answer, sidecar = facade_cv.measure(rgb, facade_id, width_m, height_m, self)
            if self.variant == "cmp-grid":
                _, openings, _ = self.cached_masks
                answer.update(metric_grid(openings, width_m, height_m))
            sidecar.update(
                engine=self.variant,
                model_revision=MODELS[self.name][1],
                segmentation=MODELS[self.name][0],
            )
            return answer, sidecar
        if self.name == "grounding-dino":
            boxes = self.detect(rgb)
            self.boxes = boxes
            answer, sidecar = facade_cv.measure(rgb, facade_id, width_m, height_m, self)
            sidecar.update(
                engine=self.variant,
                model_revision=MODELS[self.name][1],
                segmentation=MODELS[self.name][0],
                input_size=self.size,
                boxes=boxes,
            )
            return answer, sidecar
        from .facade_fast_render import schema
        from .paths import PROMPTS_DIR

        if self.short:
            prompt = (
                "Count the floors and windows of this building facade, ignoring trees and vehicles. "
                'Return only a JSON object, for example: {"wall_visible":true,"material":"plaster",'
                '"wall_colour":"#dccaa0","storeys":3,"window_columns":4}. '
                "Storeys includes ground floor. window_columns counts openings across ONE upper floor, "
                "not total windows, and is 0 for single-storey or blank walls. "
                "Choose ONE material: plaster, brick, stone, wood, concrete, glass, metal. "
                "wall_colour is the main wall surface in even daylight, in hexadecimal."
            )
        else:
            prompt = (
                Template((PROMPTS_DIR / "facade_fast_describe.md").read_text()).substitute(
                    facade_id=facade_id,
                    width_m=f"{width_m:.1f}",
                    height_m=f"{height_m:.1f}",
                    aspect=f"{width_m / height_m:.2f}:1",
                )
                + "\nJSON schema: "
                + json.dumps(schema())
            )
        messages = [
            {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}
        ]
        prompt = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        image = Image.fromarray(rgb)
        image.thumbnail((self.size, self.size))
        kwargs = dict(images=[image], text=prompt, return_tensors="pt")
        if self.name == "qwen":
            kwargs.update(min_pixels=56 * 56, max_pixels=self.size * self.size)
        inputs = self.processor(**kwargs).to(self.device)
        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.integrations.transformers import (
            build_transformers_prefix_allowed_tokens_fn,
        )

        output_schema = schema()
        if self.short:
            fields = ("wall_visible", "material", "wall_colour", "storeys", "window_columns")
            output_schema = dict(
                type="object",
                properties={k: output_schema["properties"][k] for k in fields},
                required=list(fields),
                additionalProperties=False,
            )
        prefix = build_transformers_prefix_allowed_tokens_fn(
            self.processor.tokenizer, JsonSchemaParser(output_schema)
        )
        with self.torch.inference_mode():
            ids = self.model.generate(
                **inputs,
                max_new_tokens=150 if self.short else 400,
                do_sample=False,
                prefix_allowed_tokens_fn=prefix,
            )
        text = self.processor.batch_decode(
            ids[:, inputs.input_ids.shape[1] :], skip_special_tokens=True
        )[0]
        if self.short:
            # Other fields explicitly retain the classic baseline, not VLM claims.
            answer, sidecar = facade_cv.measure(rgb, facade_id, width_m, height_m)
            try:
                values, _ = json.JSONDecoder().raw_decode(text[text.index("{") :])
                for key in ("wall_visible", "material", "wall_colour", "storeys", "window_columns"):
                    answer[key] = values[key]
                if answer["wall_visible"]:
                    answer["problem"] = ""
                errors = validate(answer, facade_id)
                if errors:
                    raise ValueError(errors)
            except (ValueError, KeyError, TypeError) as exc:
                sidecar["model_error"] = str(exc)
                answer, _ = facade_cv.measure(rgb, facade_id, width_m, height_m)
        else:
            sidecar = {}
            try:
                answer = parse_answer(text, facade_id)
            except (ValueError, TypeError) as exc:
                answer, sidecar = facade_cv.measure(rgb, facade_id, width_m, height_m)
                sidecar["model_error"] = str(exc)
        sidecar.update(
            engine=self.name,
            model_revision=MODELS[self.name][1],
            raw_text=text,
            needs_review=True,
            confidence={key: 0.0 for key in answer if key != "facade_id"},
        )
        return answer, sidecar

    def detect(self, rgb):
        image = Image.fromarray(rgb)
        inputs = self.processor(
            images=image,
            text="window. door."
            if self.variant == "grounding-dino-openings"
            else "a window. a door. a shop window. a balcony.",
            size={"shortest_edge": self.size, "longest_edge": self.size * 2},
            return_tensors="pt",
        ).to(self.device)
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=0.15 if self.variant == "grounding-dino-openings" else 0.25,
            text_threshold=0.15 if self.variant == "grounding-dino-openings" else 0.25,
            target_sizes=[rgb.shape[:2]],
        )[0]
        return [
            dict(box=box, score=score, label=label)
            for box, score, label in zip(
                result["boxes"].cpu().tolist(),
                result["scores"].cpu().tolist(),
                result["text_labels"],
                strict=True,
            )
        ]

    def masks(self, rgb):
        if self.name == "cmp":
            inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt").to(
                self.device
            )
            with self.torch.inference_mode():
                logits = self.model(**inputs).logits
                labels = (
                    self.torch.nn.functional.interpolate(
                        logits, size=rgb.shape[:2], mode="bilinear", align_corners=False
                    )
                    .argmax(1)[0]
                    .cpu()
                    .numpy()
                )
            self.cached_masks = labels == 1, np.isin(labels, [2, 3]), np.isin(labels, [0, 5])
            return self.cached_masks
        wall, _, excluded = facade_cv.classic_masks(rgb)
        openings = np.zeros(rgb.shape[:2], bool)
        h, w = openings.shape
        for record in self.boxes:
            if "balcony" in record["label"]:
                continue
            x0, y0, x1, y1 = record["box"]
            if (x1 - x0) * (y1 - y0) > h * w * 0.2:
                continue
            # Centres prevent adjacent frame boxes from merging into a solid projection.
            dx, dy = (x1 - x0) * 0.15, (y1 - y0) * 0.15
            openings[
                max(0, int(y0 + dy)) : min(h, int(y1 - dy)),
                max(0, int(x0 + dx)) : min(w, int(x1 - dx)),
            ] = True
        return wall & ~openings, openings, excluded


class MaterialModel:
    """Frozen SigLIP image/text features, prompt ensemble for the seven schema materials."""

    def __init__(self, device="mps"):
        import torch
        from transformers import AutoProcessor, SiglipModel

        from .facade_fast_render import MATERIALS

        self.torch, self.device, self.materials = torch, device, MATERIALS
        directory = ROOT / "siglip"
        if (directory / "revision.txt").read_text().strip() != MODELS["siglip"][1]:
            raise ValueError("SigLIP revision mismatch")
        torch.set_num_threads(2)
        self.processor = AutoProcessor.from_pretrained(directory, local_files_only=True)
        self.model = SiglipModel.from_pretrained(directory, local_files_only=True).eval().to(device)
        phrases = (
            "painted plaster or stucco",
            "exposed brick masonry",
            "natural stone blocks",
            "wooden boards",
            "bare concrete panels",
            "glass curtain wall",
            "metal sheet cladding",
        )
        prompts = [
            template.format(p)
            for p in phrases
            for template in (
                "a photo of a building facade made of {}",
                "the exterior wall surface is {}",
                "a close-up of {} on a building",
            )
        ]
        inputs = self.processor(text=prompts, padding="max_length", return_tensors="pt").to(device)
        with torch.inference_mode():
            features = self.model.get_text_features(**inputs)
            features = features / features.norm(dim=-1, keepdim=True)
            self.text = features.reshape(7, 3, -1).mean(1)
            self.text = self.text / self.text.norm(dim=-1, keepdim=True)

    def predict(self, rgb):
        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            features = self.model.get_image_features(**inputs)
            features = features / features.norm(dim=-1, keepdim=True)
            scores = (features @ self.text.T)[0].cpu().numpy()
        return self.materials[int(scores.argmax())], features[0].cpu().numpy(), scores


def describe(run: Path, inventory: Path, refs: Path, *, engine: str):
    model = Engine(engine, short=engine == "smolvlm")
    return facade_cv.describe(
        run, inventory, refs, measure_fn=model.measure, engine=f"facade-models-v1:{engine}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("model", choices=MODELS)
    args = parser.parse_args()
    prepare(args.model)
