"""Facade reference crops: facades GeoJSON -> best 2-4 panos per facade -> perspective crops + pose JSON.

For each facade:
 1. candidates: Google coverage tiles around the facade centre (radius), each spot resolved to its
    newest time layer; Apple Look Around coverage tiles (optional, `--sources google,lookaround`).
 2. geometry per candidate: horizontal distance d, view angle theta to the facade normal, side
    (signed angle), whether the camera is in front, projected FOV needed, Google depth occlusion
    fraction (depth np.fliplr-ed; planar proxy: catches buildings/walls, not trees).
 3. score = cos(theta)^0.8 * exp(-((d - d_opt)/d_sigma)^2) * (1 - 0.8 occ) * res_factor (+ recency tie-break);
    greedy pick with diversity (signed angle differs by >= min_sep_deg, or spot >= 6 m apart).
 4. render: pinhole crop aimed at the facade centre (mid-height), FOV fitted to the 4 corners.
    Each rectified view is screened (black fraction, sharpness); with no valid full view, more
    full views are rendered in SOURCE_ORDER until one passes.
    Outputs per view: <view>.jpg (plain crop), <view>_outline.jpg (target facade outlined in red),
    <view>_mask.png (255 facade visible, 128 facade but occluded by depth proxy, 0 not facade),
    <view>.json (pose + scores + provenance).
Camera Z = pano elevation (orthometric camera height for Google). Facade base height = facade.base_h,
else the median ground estimate (elevation - campaign camera height) of the Google candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from urllib.error import URLError

import numpy as np
from PIL import Image, ImageDraw
from requests.exceptions import RequestException
from turku3d import config

from . import camera as C
from .facades import Facade, load_facades
from .panos import PanoCache, PanoMeta, atomic_json, z17_tile
from .rectify import rectified_points, sample_wall

REFS_VERSION = "4-screened-fallback"

# Reference fallback order when the best-scored full view fails screening.
SOURCE_ORDER = ("lookaround", "google", "mapillary", "photospheres")
MAX_BLACK_FRACTION = 0.2  # more pure black is a crop outside panorama coverage
# Variance of the Laplacian over the intensity variance at SHARPNESS_SIDE px. Calibrated on
# W1-09g references: motion-blurred Look Around views 0.019 and 0.053, the softest view of an
# accepted wall 0.081 (docs/reports/w1-09h-facade-rollout.md).
MIN_SHARPNESS = 0.065
SHARPNESS_SIDE = 512


def screen_pixels(pixels: np.ndarray) -> dict:
    """Black fraction and sharpness of a rectified RGB view."""
    rgb = np.asarray(pixels)[..., :3]
    black = rgb.max(-1) <= 2
    image = Image.fromarray(rgb.astype(np.uint8)).convert("L")
    scale = SHARPNESS_SIDE / max(image.size)
    if scale < 1:
        size = (max(3, round(image.width * scale)), max(3, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.BOX)
    gray = np.asarray(image, dtype=np.float32)
    lap = gray[1:-1, :-2] + gray[1:-1, 2:] + gray[:-2, 1:-1] + gray[2:, 1:-1] - 4 * gray[1:-1, 1:-1]
    # Ignore the uncovered black area and its edge, which would read as false detail.
    valid = gray > 2
    valid = (
        valid[1:-1, 1:-1] & valid[1:-1, :-2] & valid[1:-1, 2:] & valid[:-2, 1:-1] & valid[2:, 1:-1]
    )
    inner = gray[1:-1, 1:-1][valid]
    contrast = float(inner.var()) if inner.size >= 64 else 0.0
    sharpness = float(lap[valid].var()) / contrast if contrast > 1.0 else 0.0
    return {"black_fraction": round(float(black.mean()), 4), "sharpness": round(sharpness, 4)}


def screen_problem(screen: dict) -> str | None:
    """Why a rectified view cannot serve as a generation reference, or None."""
    if screen["black_fraction"] > MAX_BLACK_FRACTION:
        return f"{screen['black_fraction']:.0%} pure black"
    if screen["sharpness"] < MIN_SHARPNESS:
        return f"blurred (sharpness {screen['sharpness']:.3f} < {MIN_SHARPNESS})"
    return None


def source_rank(source: str) -> int:
    return SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)


@dataclass
class SelectParams:
    radius: float = 45.0
    max_dist: float = 40.0
    min_front: float = 1.5
    max_angle: float = 72.0
    d_opt: float = 14.0
    d_sigma: float = 14.0
    max_views: int = 4
    min_sep_deg: float = 12.0
    max_hfov: float = 110.0
    long_side: int = 1536
    zoom: int = 5
    la_zoom: int = 0
    max_occluded: float = 0.5  # reject views whose facade is mostly behind a nearer depth plane
    fallback_per_source: int = 4  # extra full views rendered per source when none passes screening


def coverage_points(centre_en: np.ndarray, radius: float) -> list[tuple[float, float]]:
    """lat/lon samples so every z17 tile touching the radius box is queried once."""
    tiles, pts = set(), []
    for dx in np.linspace(-radius, radius, 5):
        for dy in np.linspace(-radius, radius, 5):
            lat, lon = config.en_to_wgs84(centre_en[0] + dx, centre_en[1] + dy)
            t = z17_tile(lat, lon)
            if t not in tiles:
                tiles.add(t)
                pts.append((lat, lon))
    return pts


def equirect_model(m: PanoMeta) -> C.EquirectModel:
    if m.source == "mapillary" and m.extra.get("camera_type") in ("perspective", "fisheye"):
        from .mapillary_camera import MapillaryCamera

        return MapillaryCamera(m)
    if m.source in ("google", "mapillary"):
        return C.EquirectModel(m.heading_rad, 1.0)
    # Look Around: streetlevel's stitch puts face yaw 0 at column W/2 + ... ; equirect centre is the
    # inverse travel direction; heading is CCW (0 = N, 90 = W) -> centre azimuth = -heading + pi.
    # VERIFIED 2026-10-25: LA 17922395357700297309 vs Google de5igFTgjff1c9FgktKFXQ (1.2 m apart):
    # gradient-profile cross-correlation over 360 deg gives a 2.1 deg offset (incl. parallax).
    return C.EquirectModel((-m.heading_rad + math.pi) % (2 * math.pi), 1.0)


def network_result(call, errors: list, source: str, fallback):
    """One provider outage must not discard usable references from other providers."""
    try:
        return call()
    except (RequestException, URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
        # Exception messages may contain request URLs. Keep credentials out of provenance.
        errors.append({"source": source, "error": type(exc).__name__})
        return fallback


def candidates(
    cache: PanoCache, f: Facade, P: SelectParams, sources=("google",), errors=None
) -> list[PanoMeta]:
    errors = errors if errors is not None else []
    res: dict[str, PanoMeta] = {}
    for lat, lon in coverage_points(f.centre, P.radius):
        if "google" in sources:
            for p in network_result(partial(cache.google_coverage, lat, lon), errors, "google", []):
                E, N = config.wgs84_to_en(p["lat"], p["lon"])
                if math.hypot(E - f.centre[0], N - f.centre[1]) > P.radius:
                    continue
                if np.dot(np.array([E, N]) - f.centre, f.normal) < P.min_front:
                    continue
                m = network_result(partial(cache.google_newest, p["id"]), errors, "google", None)
                if m:
                    res[f"g:{m.id}"] = m
                    if "google_history" in sources:
                        base = network_result(
                            partial(cache.google_meta, p["id"]), errors, "google", None
                        )
                        history = sorted(
                            base.extra.get("historical", []) if base else [],
                            key=lambda item: item.get("date") or "",
                        )
                        for item in history[:: max(1, len(history) // 3)][:3]:
                            older = network_result(
                                partial(cache.google_meta, item["id"]), errors, "google", None
                            )
                            if older:
                                res[f"g:{older.id}"] = older
        if "lookaround" in sources:
            for m in network_result(
                partial(cache.lookaround_coverage, lat, lon), errors, "lookaround", []
            ):
                if math.hypot(m.E - f.centre[0], m.N - f.centre[1]) <= P.radius:
                    res[f"a:{m.id}"] = m
        if "mapillary" in sources:
            for m in network_result(
                partial(cache.mapillary_coverage, lat, lon), errors, "mapillary", []
            ):
                if math.hypot(m.E - f.centre[0], m.N - f.centre[1]) <= P.radius:
                    res[f"m:{m.id}"] = m
        if "photospheres" in sources:
            for m in network_result(
                partial(cache.photosphere_coverage, lat, lon), errors, "photospheres", []
            ):
                if math.hypot(m.E - f.centre[0], m.N - f.centre[1]) <= P.radius:
                    res[f"p:{m.id}"] = m
    return list(res.values())


def base_height(f: Facade, cands: list[PanoMeta]) -> tuple[float, str]:
    if f.base_h is not None:
        return float(f.base_h), "facade.base_h"
    g = [m.ground_h for m in cands if m.source == "google" and m.ground_h is not None]
    if g:
        return float(np.median(g)), f"median(pano elevation - campaign camera height), n={len(g)}"
    la = [m.ground_h for m in cands if m.ground_h is not None]
    if la:
        return float(
            np.median(la)
        ), f"median(lookaround elevation - {cands[0].cam_height} m), n={len(la)}"
    return 3.0, "default 3.0 m (no pano elevation)"


def occlusion_fraction(
    cache: PanoCache, m: PanoMeta, cam: C.PerspectiveCam, corners: np.ndarray
) -> float | None:
    if m.source != "google":
        return None
    depth = cache.load_depth(m.id)
    if depth is None:
        return None
    small = C.PerspectiveCam(
        cam.pos,
        cam.yaw_deg,
        cam.pitch_deg,
        cam.hfov_deg,
        96,
        max(8, int(96 * cam.height / cam.width)),
    )
    t, inside = C.facade_hits(small, corners)
    u, v = equirect_model(m).uv_from_dir(small.rays(), depth.shape[1], depth.shape[0])
    dd = C.sample_nearest(depth, u, v)
    valid = inside & (dd > 0)
    if inside.sum() == 0:
        return 1.0
    return float(((dd < t - 1.5) & valid).sum() / inside.sum())


def _sub_facade(f: Facade, cam_en: np.ndarray, front: float) -> Facade | None:
    """A detail section of a long facade centred on the camera's perpendicular foot point."""
    L = f.length
    sub = float(np.clip(1.4 * front, 6.0, 14.0))
    if sub >= L * 0.8:
        return None
    s = float((cam_en - f.p0) @ f.tangent)
    s = float(np.clip(s, sub / 2, L - sub / 2))
    q0 = f.p0 + f.tangent * (s - sub / 2)
    q1 = f.p0 + f.tangent * (s + sub / 2)
    return Facade(f.id, q0, q1, f.height_m, f.base_h, f.props, f.normal_side)


def evaluate(
    cache: PanoCache, f: Facade, m: PanoMeta, base_h: float, P: SelectParams
) -> list[dict]:
    """0-2 evaluations per pano: a full-facade view and/or a partial (detail) view."""
    cam_en = np.array([m.E, m.N])
    res = []
    for part in ("full", "partial"):
        g = f if part == "full" else _sub_facade(f, cam_en, float((cam_en - f.centre) @ f.normal))
        if g is None:
            continue
        v = cam_en - g.centre
        d = float(np.linalg.norm(v))
        front = float(v @ g.normal)
        if d > P.max_dist or front < P.min_front:
            continue
        theta = C.view_angle_deg(cam_en, g.centre, g.normal)
        if theta > P.max_angle:
            continue
        signed = theta * (1 if float(v @ g.tangent) >= 0 else -1)
        corners = g.corners(base_h)
        pos = np.array(
            [m.E, m.N, m.elevation if m.elevation is not None else base_h + m.cam_height]
        )
        model = equirect_model(m)
        visible_fraction = 1.0
        if hasattr(model, "project"):
            w, h = m.extra["image_sizes"][0]
            _, _, visible = model.project(rectified_points(corners, 32) - pos, w, h)
            visible_fraction = float(visible.mean())
            if visible_fraction < 0.95:
                continue
        try:
            cam = C.fit_camera(pos, corners, P.long_side, max_hfov=180)
        except ValueError:
            continue
        if cam.hfov_deg > P.max_hfov:
            continue
        occ = occlusion_fraction(cache, m, cam, corners)
        if occ is not None and occ > P.max_occluded:
            continue
        sizes = m.extra.get("image_sizes") or [[16384, 8192]]
        W = sizes[-1][0] if m.source in ("google", "mapillary") else 16384
        focal_pixels = (
            max(m.extra["image_sizes"][0]) * model.focal
            if hasattr(model, "project")
            else W / (2 * math.pi)
        )
        ppm = focal_pixels * math.cos(math.radians(theta)) / max(d, 1.0)
        year = int(str(m.date or "2000")[:4])
        d_opt = P.d_opt if part == "full" else 10.0
        d_sig = P.d_sigma if part == "full" else 10.0
        s = math.cos(math.radians(theta)) ** 0.8 * math.exp(-(((d - d_opt) / d_sig) ** 2)) * (
            1 - 0.8 * (occ or 0.0)
        ) * min(1.0, ppm / 40) * (1.0 if part == "full" else 0.7) + 0.002 * (year - 2009)
        res.append(
            {
                "meta": m,
                "cam": cam,
                "corners": corners,
                "part": part,
                "source_visible_fraction": visible_fraction,
                "section_m": [
                    round(float((g.p0 - f.p0) @ f.tangent), 2),
                    round(float((g.p1 - f.p0) @ f.tangent), 2),
                ],
                "distance_m": round(d, 2),
                "front_m": round(front, 2),
                "view_angle_deg": round(theta, 1),
                "signed_angle_deg": round(signed, 1),
                "occluded_frac": None if occ is None else round(occ, 3),
                "px_per_m": round(ppm, 1),
                "score": round(s, 4),
            }
        )
    return res


def select(evals: list[dict], P: SelectParams) -> list[dict]:
    """Greedy by score with diversity; at least one full view when one exists; 2..max_views."""
    pool = sorted([e for e in evals if e], key=lambda e: -e["score"])
    chosen: list[dict] = []
    fulls = [e for e in pool if e["part"] == "full"]
    if fulls:
        chosen.append(fulls[0])
    for e in pool:
        if len(chosen) >= P.max_views:
            break
        if any(e is c for c in chosen):
            continue
        ok = True
        for c in chosen:
            same_spot = math.hypot(e["meta"].E - c["meta"].E, e["meta"].N - c["meta"].N) < 6.0
            same_part = e["part"] == c["part"]
            close_angle = abs(e["signed_angle_deg"] - c["signed_angle_deg"]) < P.min_sep_deg
            overlap = (
                e["part"] == "partial"
                and c["part"] == "partial"
                and abs(e["section_m"][0] - c["section_m"][0]) < 4.0
            )
            if (
                (same_spot and same_part)
                or (same_part and close_angle and e["part"] == "full")
                or overlap
            ):
                ok = False
                break
        if ok:
            chosen.append(e)
    for e in pool:
        if len(chosen) >= min(2, len(pool)):
            break
        if not any(e is c for c in chosen):
            chosen.append(e)
    return chosen


def render_view(cache: PanoCache, e: dict, out_dir: Path, view_id: str, P: SelectParams) -> dict:
    m: PanoMeta = e["meta"]
    if m.source == "google":
        src = cache.google_image(m.id, P.zoom)
    elif m.source == "mapillary":
        src = cache.mapillary_image(m)
    else:
        src = cache.lookaround_equirect(m, P.la_zoom)
    equi = cache.decoded_image(src)
    cam: C.PerspectiveCam = e["cam"]
    img = C.render(equi, equirect_model(m), cam)
    model = equirect_model(m)
    if hasattr(model, "project"):
        _, _, visible = model.project(cam.rays(), equi.shape[1], equi.shape[0])
        img[~visible] = 0
    points = rectified_points(e["corners"], P.long_side)
    directions = points - cam.pos
    rectified, visible = sample_wall(equi, model, points, cam.pos)
    rect_mask = visible.astype(np.uint8) * 255
    del equi
    t, inside = C.facade_hits(cam, e["corners"])
    mask = np.where(inside, 255, 0).astype(np.uint8)
    if m.source == "google":
        depth = cache.load_depth(m.id)
        if depth is not None:
            u, v = equirect_model(m).uv_from_dir(cam.rays(), depth.shape[1], depth.shape[0])
            dd = C.sample_nearest(depth, u, v)
            mask[inside & (dd > 0) & (dd < t - 1.5)] = 128
            u, v = equirect_model(m).uv_from_dir(directions, depth.shape[1], depth.shape[0])
            rd = C.sample_nearest(depth, u, v)
            rect_mask[(rd > 0) & (rd < np.linalg.norm(directions, axis=-1) - 1.5)] = 128
    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(out_dir / f"{view_id}.jpg", quality=92)
    Image.fromarray(mask).save(out_dir / f"{view_id}_mask.png")
    Image.fromarray(rectified.astype(np.uint8)).save(
        out_dir / f"{view_id}_rectified.jpg", quality=92
    )
    Image.fromarray(rect_mask).save(out_dir / f"{view_id}_rectified_mask.png")
    px = cam.project(e["corners"])
    ol = Image.fromarray(img).copy()
    dr = ImageDraw.Draw(ol)
    if not np.isnan(px).any():
        dr.line(
            [tuple(p) for p in px] + [tuple(px[0])],
            fill=(255, 0, 0),
            width=max(3, cam.width // 300),
        )
    ol.save(out_dir / f"{view_id}_outline.jpg", quality=90)
    rec = {
        "view_id": view_id,
        "source": m.source,
        "pano_id": m.id,
        "date": m.date,
        "pano": {
            k: m.to_dict()[k]
            for k in (
                "lat",
                "lon",
                "E",
                "N",
                "elevation",
                "heading_rad",
                "pitch_rad",
                "roll_rad",
                "cam_height",
                "ground_h",
            )
        },
        "pano_file": str(src),
        "camera": cam.to_dict(),
        "facade_corners_EN_H": [[round(float(v), 3) for v in c] for c in e["corners"]],
        "facade_corners_px": [[round(float(v), 1) for v in p] for p in px],
        "facade_visible_frac": round(float((mask == 255).sum() / max(1, (mask > 0).sum())), 3),
        "source_visible_fraction": e.get("source_visible_fraction", 1.0),
        "screen": screen_pixels(rectified),
        "source_camera_model": {
            k: m.extra.get(k)
            for k in ("camera_type", "camera_parameters", "computed_rotation", "pose_confidence")
        },
        **{
            k: e[k]
            for k in (
                "part",
                "section_m",
                "distance_m",
                "front_m",
                "view_angle_deg",
                "signed_angle_deg",
                "occluded_frac",
                "px_per_m",
                "score",
            )
        },
        "files": {
            "crop": f"{view_id}.jpg",
            "outline": f"{view_id}_outline.jpg",
            "mask": f"{view_id}_mask.png",
            "rectified": f"{view_id}_rectified.jpg",
            "rectified_mask": f"{view_id}_rectified_mask.png",
        },
        "notes": (
            "calibrated Mapillary camera rotation; approximate camera height; "
            if hasattr(model, "project")
            else "pinhole crop, no roll; pano pitch/roll not applied; depth fliplr-ed; "
        )
        + "attribution: imagery (c) Google / Apple / Mapillary contributors, reference use only, never shipped",
    }
    (out_dir / f"{view_id}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    return rec


def scaled_params(f: Facade, P: SelectParams) -> SelectParams:
    """Long facades are best seen frontally from further away (e.g. across a street or river)."""
    import dataclasses

    d_opt = max(P.d_opt, 1.5 * f.length)
    max_dist = max(P.max_dist, 2.2 * f.length)
    return dataclasses.replace(
        P,
        d_opt=d_opt,
        d_sigma=max(P.d_sigma, 0.8 * d_opt),
        max_dist=max_dist,
        radius=max(P.radius, max_dist + 5),
    )


def facade_refs(
    cache: PanoCache, f: Facade, out_root: Path, P: SelectParams, sources=("google",)
) -> dict:
    P = scaled_params(f, P)

    def geometry(feature):
        p = feature["properties"]
        return [feature["geometry"], {k: p.get(k) for k in ("id", "height_m", "base_h", "normal")}]

    key = hashlib.sha256(
        json.dumps(
            [REFS_VERSION, geometry(f.to_feature()), P.__dict__, sources], sort_keys=True
        ).encode()
    ).hexdigest()
    summary_path = out_root / f.id / "refs.json"
    if summary_path.exists():
        old = json.loads(summary_path.read_text())
        # Older rectified caches hashed all properties. An index/material change does not change a crop.
        old_key = hashlib.sha256(
            json.dumps(
                [REFS_VERSION, old["facade"], old["params"], sources], sort_keys=True
            ).encode()
        ).hexdigest()
        same_geometry = (
            geometry(old["facade"]) == geometry(f.to_feature()) and old["params"] == P.__dict__
        )
        if (
            (old.get("key") == key or (old.get("key") == old_key and same_geometry))
            and isinstance(old.get("views"), list)
            and not old.get("source_errors")
        ):
            records = [summary_path.parent / f"{v}.json" for v in old["views"]]
            if all(
                p.exists()
                and all(
                    (p.parent / n).is_file() for n in json.loads(p.read_text())["files"].values()
                )
                for p in records
            ):
                return old
    errors = []
    cands = candidates(cache, f, P, sources, errors)
    bh, bh_src = base_height(f, cands)
    evals = [x for m in cands for x in evaluate(cache, f, m, bh, P)]
    chosen = select(evals, P)
    out = out_root / f.id
    views = []
    alternatives = sorted(
        (e for e in evals if not any(e is c for c in chosen)), key=lambda e: -e["score"]
    )
    for e in (chosen + alternatives)[: P.max_views + 8]:
        k = len(views)
        view = network_result(
            lambda e=e, k=k: render_view(
                cache,
                e,
                out,
                f"{f.id}_v{k}_{e['part'][0]}_{e['meta'].source[0]}_{e['meta'].id[:12]}",
                P,
            ),
            errors,
            e["meta"].source,
            None,
        )
        if view is not None:
            views.append(view)
        if len(views) == P.max_views or (len(views) == len(chosen) and not errors):
            break
    fallback = {"needed": False, "tried": [], "found": None}
    if not any(v["part"] == "full" and screen_problem(v["screen"]) is None for v in views):
        fallback["needed"] = True
        rendered = {(v["source"], v["pano_id"], v["part"]) for v in views}
        pending = [
            e
            for e in evals
            if e["part"] == "full" and (e["meta"].source, e["meta"].id, "full") not in rendered
        ]
        pending.sort(key=lambda e: (source_rank(e["meta"].source), -e["score"]))
        tried_per_source: dict[str, int] = {}
        for e in pending:
            source = e["meta"].source
            if tried_per_source.get(source, 0) >= P.fallback_per_source:
                continue
            tried_per_source[source] = tried_per_source.get(source, 0) + 1
            k = len(views)
            view = network_result(
                lambda e=e, k=k: render_view(
                    cache, e, out, f"{f.id}_v{k}_f_{e['meta'].source[0]}_{e['meta'].id[:12]}", P
                ),
                errors,
                source,
                None,
            )
            if view is None:
                continue
            views.append(view)
            fallback["tried"].append(view["view_id"])
            if screen_problem(view["screen"]) is None:
                fallback["found"] = view["view_id"]
                break
    summary = {
        "key": key,
        "facade": f.to_feature(),
        "base_h": round(bh, 3),
        "base_h_source": bh_src,
        "n_candidates": len(cands),
        "n_valid": len(evals),
        "candidates": sorted(
            [
                {
                    "pano_id": e["meta"].id,
                    "source": e["meta"].source,
                    "date": e["meta"].date,
                    **{
                        k: e[k]
                        for k in (
                            "part",
                            "section_m",
                            "distance_m",
                            "view_angle_deg",
                            "signed_angle_deg",
                            "occluded_frac",
                            "score",
                        )
                    },
                }
                for e in evals
            ],
            key=lambda r: -r["score"],
        ),
        "views": [v["view_id"] for v in views],
        "fallback": fallback,
        "params": P.__dict__,
        "source_errors": errors,
    }
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / "refs.json", summary)
    return summary


def main(argv=None) -> int:
    from ..paths import textures_data_dir

    ap = argparse.ArgumentParser(
        prog="turkutex refs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("facades", type=Path, help="GeoJSON of facade LineStrings (EPSG:3877)")
    ap.add_argument("--out", type=Path, default=textures_data_dir("refs"))
    ap.add_argument("--only", nargs="*", help="facade ids")
    ap.add_argument("--sources", default="google", help="google,lookaround")
    ap.add_argument("--max-views", type=int, default=4)
    ap.add_argument("--zoom", type=int, default=5)
    ap.add_argument(
        "--cache", type=Path, default=None, help="pano cache root (default raw/streetview)"
    )
    a = ap.parse_args(argv)
    P = SelectParams(max_views=a.max_views, zoom=a.zoom)
    cache = PanoCache(a.cache)
    for f in load_facades(a.facades):
        if a.only and f.id not in a.only:
            continue
        s = facade_refs(cache, f, a.out, P, tuple(a.sources.split(",")))
        print(
            f"{f.id}: {s['n_candidates']} candidates, {s['n_valid']} valid, views {s['views']} -> {a.out / f.id}"
        )
    return 0
