"""Offline facade priors and a small distilled head; training never runs at describe time."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import facade_cv as cv
from . import facade_fast_render as fr

VERSION = "facade-priors-v1"
MODEL = "facebook/dinov2-small"
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
CACHE = Path.home() / "turku3d/tools/cv/cvr-priors"
PARAMETERS = Path(__file__).with_name("facade_priors_parameters.json")
FIELDS = ("storeys", "window_columns", "material", "wall_colour")


def building_group(fid: str) -> str:
    """Register aliases and LOD parts of one building must stay in the same split."""
    return fid.rsplit("_", 1)[0].removeprefix("gis-").split("-part")[0]


def colour_lab(value: str) -> np.ndarray:
    from .facade_style import lab

    return lab(np.array([int(value[i : i + 2], 16) for i in (1, 3, 5)]))


def lab_colour(value) -> str:
    rgb = cv2.cvtColor(np.asarray(value, np.float32).reshape(1, 1, 3), cv2.COLOR_LAB2RGB)
    return fr.to_hex(rgb[0, 0])


def prior(fid: str, width: float, height: float, attributes: dict, *, register=True) -> dict:
    answer = fr.register_description(
        fid, fid.rsplit("_", 1)[0], attributes if register else {}, width, height
    )
    answer["storeys"] = int(np.clip(answer["storeys"], 1, 30))
    answer["window_columns"] = (
        int(np.clip(answer["window_columns"], 0, 60)) if answer["storeys"] > 1 else 0
    )
    return answer


def geometry_features(width: float, height: float, attributes: dict) -> list[float]:
    levels = attributes.get("levels") or 0
    year = attributes.get("year") or 0
    return [
        width,
        height,
        np.log1p(width),
        np.log1p(height),
        width / max(height, 0.1),
        levels,
        float(bool(levels)),
        height / max(levels, 1),
        (year - 1900) / 100 if year else 0,
        float(bool(year)),
        *[float(attributes.get("material_class") == m) for m in fr.MATERIAL_OF_CLASS],
        float(bool(attributes.get("material"))),
    ]


def image_features(rgb: np.ndarray, answer: dict, sidecar: dict) -> list[float]:
    small = cv2.resize(rgb, (64, 64))
    lab = cv2.cvtColor(small.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    result = [answer["storeys"], answer["window_columns"], float(answer["wall_visible"])]
    result += list(colour_lab(answer["wall_colour"]))
    result += [sidecar["confidence"][k] for k in ("storeys", "window_columns", "wall_colour")]
    result += [sidecar["occlusion_share"]]
    result += list(np.quantile(lab.reshape(-1, 3), [0.1, 0.25, 0.5, 0.75, 0.9], axis=0).ravel())
    for band in np.array_split(lab, 4):
        result += list(band.mean((0, 1))) + list(band.std((0, 1)))
    return list(map(float, result))


def fit_head(x, descriptions, alpha=100.0) -> dict:
    """Standardised multi-output ridge, exported as JSON; no estimator pickle required."""
    x = np.asarray(x, float)
    mean, scale = x.mean(0), np.maximum(x.std(0), 0.1)
    z = (x - mean) / scale
    y = np.asarray(
        [
            [d["storeys"], d["window_columns"], *colour_lab(d["wall_colour"])]
            + [float(d["material"] == m) for m in fr.MATERIALS]
            for d in descriptions
        ]
    )
    offset = y.mean(0)
    # Dual solve is cheaper for the frozen 768-dimensional embedding on this small dataset.
    weights = z.T @ np.linalg.solve(z @ z.T + alpha * np.eye(len(z)), y - offset)
    return dict(
        mean=mean.tolist(), scale=scale.tolist(), weights=weights.tolist(), offset=offset.tolist()
    )


def predict_head(head: dict, x) -> dict:
    if head.get("kind") == "forest":
        result = {}
        for field, forest in head["fields"].items():
            values = []
            for tree in forest["trees"]:
                node = tree
                while len(node) == 4:
                    node = node[2] if x[node[0]] <= node[1] else node[3]
                values.append(node[0])
            value = np.mean(values, axis=0)
            result[field] = (
                lab_colour(value)
                if field == "wall_colour"
                else forest["classes"][int(np.argmax(value))]
            )
        if result.get("storeys", 2) <= 1 and "window_columns" in result:
            result["window_columns"] = 0
        return result
    y = ((np.asarray(x) - head["mean"]) / head["scale"]) @ np.asarray(head["weights"]) + head[
        "offset"
    ]
    storeys = int(np.clip(np.rint(y[0]), 1, 30))
    return dict(
        storeys=storeys,
        window_columns=int(np.clip(np.rint(y[1]), 0, 60)) if storeys > 1 else 0,
        wall_colour=lab_colour(y[2:5]),
        material=fr.MATERIALS[int(np.argmax(y[5:]))],
    )


def fit_forest(x, descriptions) -> dict:
    """Small nonlinear comparator. sklearn is needed only to train, never for inference."""
    from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor

    result = dict(kind="forest", fields={})
    for field in FIELDS:
        regression = field == "wall_colour"
        y = [colour_lab(d[field]) if regression else d[field] for d in descriptions]
        model = (ExtraTreesRegressor if regression else ExtraTreesClassifier)(
            n_estimators=64, min_samples_leaf=3, max_depth=8, random_state=17, n_jobs=1
        ).fit(x, y)

        def encode(tree, index=0):
            if tree.children_left[index] < 0:
                return [tree.value[index].ravel().tolist()]
            return [
                int(tree.feature[index]),
                float(tree.threshold[index]),
                encode(tree, tree.children_left[index]),
                encode(tree, tree.children_right[index]),
            ]

        result["fields"][field] = dict(
            classes=[] if regression else model.classes_.tolist(),
            trees=[encode(estimator.tree_) for estimator in model.estimators_],
        )
    return result


class Embeddings:
    """Frozen DINOv2 CLS + mean patch token, full crop resized to 224 square."""

    def __init__(self):
        import torch
        from transformers import AutoModel

        self.torch = torch
        torch.set_num_threads(2)
        provenance = json.loads((CACHE / "dinov2/provenance.json").read_text())
        if provenance["model"] != MODEL or provenance["revision"] != REVISION:
            raise ValueError("DINOv2 cache revision mismatch; run prepare-model")
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = (
            AutoModel.from_pretrained(CACHE / "dinov2", local_files_only=True)
            .eval()
            .to(self.device)
        )

    def __call__(self, rgb):
        torch = self.torch
        image = Image.fromarray(rgb).resize((224, 224), Image.Resampling.BICUBIC)
        x = np.asarray(image, np.float32) / 255
        x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        batch = torch.tensor(x.transpose(2, 0, 1)[None], dtype=torch.float32, device=self.device)
        with torch.inference_mode():
            tokens = self.model(pixel_values=batch).last_hidden_state
            return torch.cat([tokens[:, 0], tokens[:, 1:].mean(1)], dim=1)[0].cpu().numpy()


def prepare_model():
    """Explicit publisher download. Inference never downloads weights."""
    import requests

    directory = CACHE / "dinov2"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = dict(model=MODEL, revision=REVISION, files={})
    for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
        target = directory / name
        with requests.get(
            f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=120, stream=True
        ) as response:
            response.raise_for_status()
            temp = target.with_suffix(".partial")
            with temp.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
            temp.replace(target)
        manifest["files"][name] = dict(
            bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest()
        )
    (directory / "provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


class Engine:
    def __init__(self, parameters: Path = PARAMETERS):
        self.parameters = json.loads(parameters.read_text())
        if self.parameters["version"] != VERSION:
            raise ValueError("incompatible priors parameters")
        self.embedding = Embeddings() if self.parameters.get("embedding") else None

    def measure(self, rgb, fid, width, height, attributes):
        answer, sidecar = cv.measure(rgb, fid, width, height)
        candidates = {
            "geometry": prior(fid, width, height, {}, register=False),
            "register": prior(fid, width, height, attributes),
            "classic": answer.copy(),
        }
        vectors = {
            "priors": geometry_features(width, height, attributes),
            "appearance": image_features(rgb, answer, sidecar),
        }
        if self.embedding:
            vectors["dino"] = self.embedding(rgb).tolist()
        for name, model in self.parameters["heads"].items():
            features = sum((vectors[k] for k in model["features"]), [])
            candidates[name] = predict_head(model["head"], features)
        for field, source in self.parameters["fusion"].items():
            answer[field] = candidates[source][field]
        if answer["storeys"] <= 1:
            answer["window_columns"] = 0
        sidecar.update(
            engine=VERSION,
            fusion=self.parameters["fusion"],
            calibration=self.parameters["calibration"],
            needs_review=True,
        )
        for field, calibration in self.parameters["calibration"].items():
            sidecar["confidence"][field] = calibration["probability"]
        if not answer["wall_visible"]:
            for field in FIELDS:
                sidecar["confidence"][field] = 0.0
        return answer, sidecar


def describe(run: Path, inventory: Path, refs: Path):
    from turku3d import config

    from . import facade_fast as fast
    from .facade_derive import building_attributes

    _, facades = fast.planned_facades(run, inventory)
    attributes = building_attributes(
        config.raw_dir("wfs/work-3x3"), {f.props["building_id"] for f in facades}
    )
    engine = Engine()
    output = run / "run_describe/out.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and any(
        json.loads(line).get("model") != VERSION for line in output.read_text().splitlines()
    ):
        raise ValueError("use a separate run directory for cv-priors")
    count, failures, reasons = 0, {}, {}
    start = time.monotonic()
    parameter_sha = fast.sha(PARAMETERS)
    with output.open("w") as stream:
        for facade in facades:
            view, reason = fast.best_view(facade, refs)
            if view is None:
                reasons[facade.id] = reason
                continue
            try:
                crop = fast.crop_for_call(facade, view, refs, run / "inputs")
                with Image.open(crop) as image:
                    answer, sidecar = engine.measure(
                        np.asarray(image.convert("RGB")),
                        facade.id,
                        facade.length,
                        facade.height_m,
                        attributes.get(facade.props["building_id"], {}),
                    )
                if errors := fr.validate(answer, facade.id):
                    raise ValueError(errors)
                reference = dict(
                    source=view["source"], view_id=view["view_id"], crop_sha256=fast.sha(crop)
                )
                key = hashlib.sha256(
                    (
                        VERSION
                        + parameter_sha
                        + reference["crop_sha256"]
                        + json.dumps([facade.length, facade.height_m])
                        + json.dumps(
                            attributes.get(facade.props["building_id"], {}), sort_keys=True
                        )
                    ).encode()
                ).hexdigest()
                fast.write_json(run / "confidence" / f"{facade.id}.json", sidecar)
                stream.write(
                    json.dumps(
                        dict(
                            id=f"{facade.id}.fast",
                            ok=True,
                            answer=answer,
                            model=VERSION,
                            effort="local",
                            key=key,
                            cached=False,
                            meta=dict(reference=reference, cv=sidecar),
                        )
                    )
                    + "\n"
                )
                count += 1
            except Exception as exc:  # noqa: BLE001 - per-wall failure is retained
                failures[facade.id] = f"{type(exc).__name__}: {exc}"
    result = dict(
        engine=VERSION,
        walls=len(facades),
        jobs=count,
        no_reference=reasons,
        failed=failures,
        calls=0,
        seconds=round(time.monotonic() - start, 3),
        exit=int(bool(failures)),
    )
    fast.write_json(run / "describe.json", result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-model"])
    parser.parse_args()
    prepare_model()
