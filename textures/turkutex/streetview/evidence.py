"""Shared semantic/foreground reference screen using local pinned models only."""

from __future__ import annotations

from threading import Lock

import numpy as np

_LOCK = Lock()
_MODEL = None
_DETECTOR = None
VERSION = "ade20k-dino-foreground-v1"


def box_coverage(shape: tuple, boxes: list) -> float:
    """Union of conservative foreground boxes, without double-counting overlaps."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        x0, x1 = np.clip([int(np.floor(x0)), int(np.ceil(x1))], 0, width)
        y0, y1 = np.clip([int(np.floor(y0)), int(np.ceil(y1))], 0, height)
        mask[y0:y1, x0:x1] = True
    return float(mask.mean())


def measure(rgb: np.ndarray) -> dict:
    global _MODEL, _DETECTOR
    from PIL import Image

    from ..facade_hybrid import Evidence
    from ..facade_models import Engine

    with _LOCK:
        if _MODEL is None:
            _MODEL = Evidence()
            _DETECTOR = Engine("grounding-dino", size=800)
        evidence = _MODEL.measure(rgb)
        # Very narrow wall fragments do not provide a stable detector feature map.
        # They retain ADE20K evidence rather than padding invented facade detail.
        detections = []
        if min(rgb.shape[:2]) >= 32 and max(rgb.shape[:2]) / min(rgb.shape[:2]) <= 12:
            inputs = _DETECTOR.processor(
                images=Image.fromarray(rgb),
                text="a tree. a bus. a car. a truck. a person. a fence. scaffolding.",
                size={"shortest_edge": 800, "longest_edge": 1600},
                return_tensors="pt",
            ).to(_DETECTOR.device)
            with _DETECTOR.torch.inference_mode():
                outputs = _DETECTOR.model(**inputs)
            result = _DETECTOR.processor.post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=0.3,
                text_threshold=0.25,
                target_sizes=[rgb.shape[:2]],
            )[0]
            for box, score, label in zip(
                result["boxes"].cpu().tolist(),
                result["scores"].cpu().tolist(),
                result["text_labels"],
                strict=True,
            ):
                if any(
                    name in label
                    for name in ("tree", "bus", "car", "truck", "person", "fence", "scaffolding")
                ):
                    detections.append(dict(box=box, score=score, label=label))
        detected = box_coverage(rgb.shape, [row["box"] for row in detections])
        return dict(
            evidence,
            state="measured",
            version=VERSION,
            semantic_occlusion_share=evidence["occlusion_share"],
            detector_box_share=detected,
            detections=detections,
            occlusion_share=max(evidence["occlusion_share"], detected),
        )


def problem(evidence: dict) -> str | None:
    if evidence.get("state") != "measured":
        return "semantic reference screen unavailable"
    if evidence["occlusion_share"] > 0.2:
        return f"foreground/off-wall coverage proxy {evidence['occlusion_share']:.0%} > 20%"
    if evidence["facade_share"] < 0.65:
        return f"facade support {evidence['facade_share']:.0%} < 65%"
    if evidence.get("multiple_surfaces"):
        return "multiple facade surfaces"
    return None
