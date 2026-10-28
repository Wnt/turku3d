"""Offline field-wise facade fusion, with explicit abstentions and register fallback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from . import facade_cv as cv
from . import facade_priors as p

VERSION = "facade-hybrid-v1"
PARAMETERS = Path(__file__).with_name("facade_hybrid_parameters.json")
FIELDS = ("storeys", "window_columns", "material", "wall_colour")


def bay_spacing(material: str, era: str | None) -> float:
    return 2.6 if material == "wood" or era in ("pre-1850", "1850-1919") else 3.0


def field_outputs(prediction: dict, evidence: dict) -> dict:
    """Thresholds describe evidence sufficiency, not calibrated model probabilities."""
    common = []
    if evidence.get("screen_problem"):
        common.append(evidence["screen_problem"])
    if evidence.get("wrong_wall") or evidence.get("facade_share", 0) < 0.2:
        common.append("insufficient target facade")
    if evidence.get("multiple_surfaces"):
        common.append("multiple facade surfaces")
    if evidence.get("width_m", 0) < 3 or evidence.get("minimum_pixels", 0) < 32:
        common.append("narrow or unresolved wall")
    output = {}
    for field in FIELDS:
        reasons = list(common)
        limit = 0.25 if field in ("storeys", "window_columns") else 0.4
        if evidence.get("occlusion_share", 1) > limit:
            reasons.append("foreground occlusion")
        if field == "storeys" and (not evidence.get("opening_boxes", 0) or prediction[field] < 1):
            reasons.append("no detected opening rows")
        if field in ("wall_colour", "material") and evidence.get("wall_share", 0) < 0.12:
            reasons.append("insufficient opaque wall")
        output[field] = dict(
            value=None if reasons else prediction[field],
            state="abstain" if reasons else "measured",
            reason="; ".join(reasons),
        )
    return output


def merge_fields(register: dict, fields: dict) -> dict:
    """Keep each abstaining field's register value, including when another field succeeds."""
    result = dict(register)
    for field in FIELDS:
        row = fields[field]
        if row["state"] == "measured" and row["value"] is not None:
            result[field] = row["value"]
    result["wall_visible"] = any(r["state"] == "measured" for r in fields.values())
    result["problem"] = "" if result["wall_visible"] else "hybrid abstained on every field"
    if result["storeys"] <= 1:
        result["window_columns"] = 0
    return result


def surface_discontinuity(rgb: np.ndarray, wall: np.ndarray) -> bool:
    """Conservative vertical colour-boundary heuristic, not wall identity recognition.

    Require a chromatic split in both height halves, ignoring luminance-only shade.
    Same-colour neighbouring walls and unusual painted facades remain ambiguous.
    """
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    h, w = wall.shape
    differences = []
    for y0, y1 in ((0, h // 2), (h // 2, h)):
        sides = []
        for x0, x1 in ((0, w // 3), (2 * w // 3, w)):
            pixels = lab[y0:y1, x0:x1][wall[y0:y1, x0:x1]]
            if len(pixels) < max(32, (y1 - y0) * (x1 - x0) * 0.25):
                return False
            sides.append(np.median(pixels[:, 1:], axis=0))
        differences.append(sides[0] - sides[1])
    return bool(
        all(np.linalg.norm(d) > 18 for d in differences)
        and np.dot(differences[0], differences[1]) > 0
    )


class Evidence:
    """ADE20K semantic evidence mask on MPS; no network and no learned quality claims."""

    def __init__(self):
        self.segmenter = cv.Segmenter()
        self.device = "mps" if self.segmenter.torch.backends.mps.is_available() else "cpu"
        self.segmenter.model.to(self.device)

    def measure(self, rgb):
        from PIL import Image

        s = self.segmenter
        inputs = s.processor(images=Image.fromarray(rgb), return_tensors="pt").to(self.device)
        with s.torch.inference_mode():
            logits = s.model(**inputs).logits
            labels = (
                s.torch.nn.functional.interpolate(
                    logits, size=rgb.shape[:2], mode="bilinear", align_corners=False
                )
                .argmax(1)[0]
                .cpu()
                .numpy()
            )
        wall = np.isin(labels, [0, 1, 25])
        facade = wall | np.isin(labels, [8, 14])
        excluded = np.isin(labels, [2, 4, 6, 9, 11, 12, 17, 20, 21, 80, 83, 90, 102, 116, 127])
        # Semantic classes cannot prove that a building is the intended building. Explicit
        # reference audits, when supplied, override these conservative pixel checks.
        return dict(
            facade_share=float(facade.mean()),
            wall_share=float(wall.mean()),
            occlusion_share=float(excluded.mean()),
            multiple_surfaces=surface_discontinuity(rgb, wall),
        )


class FeatureExtractor:
    def __init__(self):
        from .facade_models import Engine as Models

        self.detector = Models("grounding-dino-openings", size=800)
        self.embedding = p.Embeddings()
        self.evidence = Evidence()

    def extract(self, rgb, fid, width, height):
        """Reusable features; fitting and evaluation can reuse them without more inference."""
        from .streetview.refs import screen_pixels, screen_problem

        evidence = self.evidence.measure(rgb)
        evidence.update(
            width_m=width,
            minimum_pixels=min(rgb.shape[:2]),
            screen_problem=screen_problem(screen_pixels(rgb)),
        )
        classic, meta = cv.measure(rgb, fid, width, height)
        if width >= 3 and min(rgb.shape[:2]) >= 32 and evidence["facade_share"] >= 0.2:
            counts, detector_meta = self.detector.measure(rgb, fid, width, height)
            evidence["opening_boxes"] = len(detector_meta["boxes"])
        else:
            counts = {"storeys": 0}
            evidence["opening_boxes"] = 0
        return dict(
            storeys=counts["storeys"],
            evidence=evidence,
            classic=classic,
            appearance=p.image_features(rgb, classic, meta),
            embedding=self.embedding(rgb).tolist(),
        )


class Fusion:
    def __init__(self, parameters: dict):
        self.parameters = parameters
        if self.parameters["version"] != VERSION:
            raise ValueError("hybrid parameter version mismatch")
        self.parameter_sha = hashlib.sha256(
            json.dumps(parameters, sort_keys=True).encode()
        ).hexdigest()

    def predict(self, features, fid, width, height, attributes=None, *, audit=None):
        attributes = attributes or {}
        register = p.prior(fid, width, height, attributes)
        learned = p.predict_head(self.parameters["head"], features["embedding"])
        source = self.parameters["material_source"]
        material = (
            learned["material"]
            if source == "embedding"
            else features["classic"]["material"]
            if source == "classic"
            else register["material"]
        )
        prediction = dict(
            storeys=features["storeys"],
            wall_colour=learned["wall_colour"],
            material=material,
            window_columns=int(
                np.clip(round(width / bay_spacing(material, attributes.get("era"))), 0, 60)
            ),
        )
        evidence = {**features["evidence"], **(audit or {})}
        fields = field_outputs(prediction, evidence)
        floors = (
            fields["storeys"]["value"]
            if fields["storeys"]["state"] == "measured"
            else register["storeys"]
        )
        if floors <= 1 and fields["window_columns"]["state"] == "measured":
            fields["window_columns"]["value"] = 0
        answer = merge_fields(register, fields)
        return answer, dict(
            engine=VERSION,
            parameter_sha256=self.parameter_sha,
            fields=fields,
            evidence=evidence,
            needs_review=True,
            calls=0,
            sources=dict(
                storeys="grounding-dino-800",
                wall_colour="distilled-dinov2",
                material=source,
                window_columns="material-era-bay-spacing",
            ),
        )


class Engine(FeatureExtractor, Fusion):
    def __init__(self, parameters: Path = PARAMETERS):
        Fusion.__init__(self, json.loads(parameters.read_text()))
        FeatureExtractor.__init__(self)

    def measure(self, rgb, fid, width, height, attributes=None, *, audit=None):
        return self.predict(
            self.extract(rgb, fid, width, height), fid, width, height, attributes, audit=audit
        )


def describe(run: Path, inventory: Path, refs: Path):
    from turku3d import config

    from . import facade_fast as fast
    from .facade_derive import building_attributes

    _, facades = fast.planned_facades(run, inventory)
    attrs = building_attributes(
        config.raw_dir("wfs/work-3x3"), {f.props["building_id"] for f in facades}
    )
    by_id = {f.id: attrs.get(f.props["building_id"], {}) for f in facades}
    model = Engine()
    return cv.describe(
        run,
        inventory,
        refs,
        measure_fn=lambda rgb, fid, w, h: model.measure(rgb, fid, w, h, by_id[fid]),
        engine=VERSION + ":" + model.parameter_sha,
    )
