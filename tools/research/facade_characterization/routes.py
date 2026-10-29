"""Resumable route-distance facade survey. Evidence stays outside the repository."""

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from pose import refine
from shapely import STRtree
from shapely.geometry import LineString, Point
from turku3d import config
from turku3d.routes_rings import graph_routes
from turkutex.codex.usage import snapshot
from turkutex.streetview.camera import EquirectModel
from turkutex.streetview.facades import load_facades
from turkutex.streetview.panos import PanoMeta
from turkutex.streetview.rectify import rectified_points, sample_wall
from turkutex.streetview.refs import SelectParams, equirect_model, evaluate


def save(path, value):
    """Immutable snapshots: a restart must not silently replace survey evidence."""
    with path.open("x") as f:
        json.dump(value, f, indent=1, allow_nan=False)
        f.write("\n")


def event(root, **values):
    with (root / "events.jsonl").open("a") as f:
        f.write(json.dumps(dict(time=time.time(), **values), allow_nan=False) + "\n")


def statuses(root):
    state = {}
    if (root / "events.jsonl").exists():
        for line in (root / "events.jsonl").read_text().splitlines():
            e = json.loads(line)
            if "wall_id" in e:
                state[e["wall_id"]] = e
    return state


def queue_rows(root):
    rows = json.loads((root / "queue.json").read_text())
    for path in sorted(root.glob("queue-additions-*.json")):
        rows.extend(json.loads(path.read_text()))
    if len({r["wall_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate wall in queue additions")
    return sorted(rows, key=lambda r: (r["route_distance_m"], r["wall_id"]))


def make_queue(root):
    nav = json.loads((root / "inputs/navigation.json").read_text())
    tertiary = json.loads((root / "inputs/tertiary.json").read_text())
    lines, edges, distances, corridor, routes = graph_routes(nav, tertiary)
    save(root / "routes.json", routes)
    assets = json.loads((root / "inputs/assets.json").read_text())
    stored = set()

    def identities(item):
        if isinstance(item, dict):
            for k, v in item.items():
                if k in ("building_id", "building_key") and isinstance(v, str):
                    stored.add(v.removeprefix("gis-").split("-part")[0])
                elif k == "bound" and isinstance(v, list):
                    stored.update(x.rsplit("_", 1)[0].removeprefix("gis-") for x in v)
                else:
                    identities(v)
        elif isinstance(item, list):
            for v in item:
                identities(v)

    identities(assets)
    tree = STRtree([lines[i] for i in corridor])
    alltree = STRtree(lines)
    walls = load_facades(root / "inputs/whole-walls.geojson")
    rows = []
    for wall in walls:
        center = wall.centre - [config.ORIGIN_E, config.ORIGIN_N]
        center[1] *= -1
        normal = wall.normal * [1, -1]
        point = Point(center)
        candidates = []
        for j in tree.query(point.buffer(65)):
            i = corridor[j]
            line = lines[i]
            t = line.project(point)
            target = np.array(line.interpolate(t).coords[0])
            delta = target - center
            if delta @ normal < max(1, np.linalg.norm(delta) * 0.35):
                continue
            a, b = edges[i][:2]
            distance = min(distances[a] + t, distances[b] + line.length - t)
            if np.isfinite(distance):
                candidates.append((point.distance(line), distance, i))
        is_stored = wall.props["building_id"] in stored
        if not candidates and not is_stored:
            continue
        if candidates:
            lateral, distance, edge = min(candidates)
        else:
            edge = int(alltree.nearest(point))
            line = lines[edge]
            t = line.project(point)
            a, b = edges[edge][:2]
            lateral = point.distance(line)
            distance = min(distances[a] + t, distances[b] + line.length - t)
        if not np.isfinite(distance):
            continue
        # Left-to-right as seen from outside is essential for form row order.
        corners = wall.corners(wall.base_h)
        rows.append(
            dict(
                wall_id=wall.id,
                building_id=wall.props["building_id"],
                p0=corners[0, :2].tolist(),
                p1=corners[1, :2].tolist(),
                normal=wall.normal.tolist(),
                width_m=wall.length,
                height_m=wall.height_m,
                base_h=wall.base_h,
                route_distance_m=round(float(distance), 2),
                street_distance_m=round(lateral, 2),
                road_edge=edge,
                ring=int(distance // 150),
                stored_building=is_stored,
                status="pending",
            )
        )
    rows.sort(key=lambda r: (r["route_distance_m"], r["wall_id"]))
    save(root / "queue.json", rows)
    save(
        root / "queue-summary.json",
        dict(
            walls=len(rows),
            stored_buildings=len(stored),
            inventoried_stored_buildings=len(
                {r["building_id"] for r in rows if r["stored_building"]}
            ),
            destinations=len(nav["addresses"]["addresses"]),
            landmarks=sum(bool(d.get("landmark")) for d in nav["addresses"]["addresses"]),
            rings=dict(Counter(r["ring"] for r in rows)),
            ring_width_m=150,
            missing_stored_ids=sorted(stored - {r["building_id"] for r in rows}),
        ),
    )
    print((root / "queue-summary.json").read_text())


class NoDepth:
    def load_depth(self, _):
        return None


def prepare(root, batch, count, alternate_from=None, wall_ids=None, source=None):
    started = time.time()
    usage = snapshot()
    if usage["used_percent"] is None or usage["used_percent"] >= 82:
        raise RuntimeError(f"No new reading allowed: quota {usage}")
    directory = root / "batches" / batch
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / "start.json", dict(time=started, quota=usage, source_filter=source))
    states = statuses(root)
    originals = {}
    if alternate_from:
        originals = {
            r["wall_id"]: r
            for r in json.loads((root / "batches" / alternate_from / "refined.json").read_text())
        }
        pending = [r for r in originals.values() if not wall_ids or r["wall_id"] in wall_ids]
    elif wall_ids:
        pending = [r for r in queue_rows(root) if r["wall_id"] in set(wall_ids)]
    else:
        pending = [r for r in queue_rows(root) if r["wall_id"] not in states]
    if not pending:
        raise RuntimeError("No pending walls")
    ring = pending[0]["ring"]
    pending = [r for r in pending if r["ring"] == ring][:count]
    rejected_sources = root / "source-rejections.jsonl"
    excluded = {
        (r["source"], r["pano_id"])
        for r in (
            map(json.loads, rejected_sources.read_text().splitlines())
            if rejected_sources.exists()
            else []
        )
    }
    panos = [
        p
        for p in json.loads((root / "inputs/panoramas.json").read_text())
        if p["cached_images"]
        and (p["source"], p["id"]) not in excluded
        and (source is None or p["source"] == source)
    ]
    xy = np.array([[p["E"], p["N"]] for p in panos])
    from turkutex.streetview.facades import Facade

    selection = []
    for row in pending:
        wall = Facade(
            row["wall_id"], np.array(row["p0"]), np.array(row["p1"]), row["height_m"], row["base_h"]
        )
        delta = xy - wall.centre
        distance = np.linalg.norm(delta, axis=1)
        cosine = delta @ wall.normal / np.maximum(distance, 0.01)
        indices = np.flatnonzero((distance < 65) & (cosine > 0.5) & (distance > 4))
        indices = sorted(indices, key=lambda i: -cosine[i] / (1 + abs(distance[i] - 22) / 25))
        selected = None
        for i in indices[:12]:
            p = panos[i]
            if row["wall_id"] in originals:
                previous = originals[row["wall_id"]]["view"]["pano"]
                if p["id"] == previous["id"] or (
                    p.get("date") == previous.get("date")
                    and np.linalg.norm(xy[i] - [previous["E"], previous["N"]]) < 4
                ):
                    continue
            views = evaluate(
                NoDepth(),
                wall,
                PanoMeta.from_dict(p),
                wall.base_h,
                SelectParams(max_dist=65, max_hfov=145),
            )
            full = next((v for v in views if v["part"] == "full"), None)
            if full is None:
                continue
            selected = dict(
                source=p["source"],
                pano_id=p["id"],
                pano=p,
                panorama=max(p["cached_images"], key=lambda v: v["bytes"])["path"],
                image_to_grid=equirect_model(full["meta"]).rotation.tolist(),
                camera=dict(pos_EN_H=full["cam"].pos.tolist()),
                camera_height_source=full["camera_height_source"],
                facade_corners_EN_H=full["corners"].tolist(),
                lod2_visibility=full["lod2_visibility"],
            )
            break
        if selected:
            selection.append(dict(row, view=selected))
        elif states.get(row["wall_id"], {}).get("status") == "done":
            # A missing alternate cannot invalidate an already readable view.
            event(
                root,
                **dict(
                    {k: v for k, v in states[row["wall_id"]].items() if k != "time"},
                    alternate_batch=batch,
                    alternate_unavailable=True,
                ),
            )
        else:
            event(
                root,
                wall_id=row["wall_id"],
                batch=batch,
                ring=ring,
                status="rejected",
                reason="No cached panorama passes whole-wall visibility and viewing-angle checks",
            )
    save(directory / "selection.json", selection)
    paths = sorted({r["view"]["panorama"] for r in selection})
    (directory / "rsync-files.txt").write_text("".join(p + "\n" for p in paths))
    subprocess.run(
        [
            "rsync",
            "-a",
            "--files-from=" + str(directory / "rsync-files.txt"),
            "-e",
            f"ssh -i {Path.home()}/.ssh/oci_cuttlefish -o BatchMode=yes",
            "ubuntu@82.70.50.61:/srv/turku3d/data/cache/streetlevel/",
            str(directory / "panoramas") + "/",
        ],
        check=True,
    )
    crops = directory / "crops"
    crops.mkdir(exist_ok=True)
    by_pano = defaultdict(list)
    for row in selection:
        by_pano[row["view"]["panorama"]].append(row)
    for path, rows in by_pano.items():
        source = directory / "panoramas" / path
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with Image.open(source) as im:
            image = np.array(im.convert("RGB"))
        for row in rows:
            view = row["view"]
            rotation, correction = refine(image, view)
            view["refinement"] = correction
            view["refined_image_to_grid"] = rotation.tolist()
            view["panorama_sha256"] = digest
            points = rectified_points(
                view["facade_corners_EN_H"], pixels_per_metre=40, max_side=1800
            )
            crop, _ = sample_wall(
                image, EquirectModel(0, rotation=rotation), points, view["camera"]["pos_EN_H"]
            )
            filename = crops / f"{row['wall_id']}.jpg"
            Image.fromarray(np.clip(crop, 0, 255).astype("uint8")).save(filename, quality=95)
            row["crop_sha256"] = hashlib.sha256(filename.read_bytes()).hexdigest()
        del image
        # Only this batch's fetched copies; original/source panoramas are untouched.
        source.unlink()
    save(directory / "refined.json", selection)
    sheet = Image.new("RGB", (1800, 360 * ((len(selection) + 1) // 2)), "#efefeb")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(selection):
        x, y = i % 2 * 900, i // 2 * 360
        with Image.open(crops / f"{row['wall_id']}.jpg") as im:
            thumb = ImageOps.contain(im, (890, 320))
        sheet.paste(thumb, (x + (900 - thumb.width) // 2, y + 38))
        draw.text(
            (x + 5, y + 3),
            f"{i + 1:02} {row['wall_id']} | {row['route_distance_m']:.0f}m | {row['width_m']:.1f}x{row['height_m']:.1f}m",
            fill="black",
        )
        correction = row["view"]["refinement"]
        draw.text(
            (x + 5, y + 20),
            f"pose refined: {correction['accepted']} | {row['view']['pano']['date']}",
            fill="black",
        )
    if selection:
        sheet.save(directory / "contact.jpg", quality=96)
    save(
        directory / "prepared.json",
        dict(
            seconds=time.time() - started,
            walls=len(selection),
            rejected=len(pending) - len(selection),
            ring=ring,
        ),
    )
    print(
        directory, len(selection), "walls", round(time.time() - started, 1), "seconds", flush=True
    )


def compile_batch(root, batch, publish):
    from turkutex.facade_derive import building_attributes
    from turkutex.facade_program import merge_building
    from turkutex.facade_structure import compile_structure, validate

    directory = root / "batches" / batch
    rows = {r["wall_id"]: r for r in json.loads((directory / "refined.json").read_text())}
    forms = json.loads((directory / "answers.json").read_text())
    if len(forms) != len(rows) or {f["wall_id"] for f in forms} != set(rows):
        raise ValueError("Every selected wall requires exactly one answer")
    for form in forms:
        validate(form)
    attributes = building_attributes(
        config.raw_dir("wfs/work-3x3"), {r["building_id"] for r in rows.values()}
    )
    register_path = root / "inputs/register.json"
    register = json.loads(register_path.read_text()) if register_path.exists() else {}
    for row in rows.values():
        attrs = attributes[row["building_id"]]
        for key, val in register.get(row["building_id"], {}).items():
            if val is not None:
                attrs[key] = val
        # Stored binding attributes are original register covariates, not the AI
        # description's inferred storey count. Use them only to fill missing data.
        for key, val in row.get("attributes", {}).items():
            if attrs.get(key) is None and val is not None:
                attrs[key] = val
    programs = {p["building_id"]: p for p in json.loads(publish.read_text())}
    accepted = []
    rejected = []
    for form in forms:
        row = rows[form["wall_id"]]
        bid = row["building_id"]
        if form.get("reject"):
            rejected.append(form)
            continue
        wall = compile_structure(row, form, attributes[bid].get("levels"))
        accepted.append(dict(building_id=bid, wall=wall))
        program = programs.setdefault(bid, merge_building(bid, attributes[bid], []))
        # The earlier pilot has explicit observed rectangles. Preserve those when
        # the new count-only survey sees exactly the same span.
        line = LineString([wall["geometry"]["p0"], wall["geometry"]["p1"]])
        existing = False
        for wall_index, old in enumerate(program["walls"]):
            if form.get("revision_of") and old["wall_id"] == wall["wall_id"] and "structure" in old:
                program["walls"][wall_index] = wall
                existing = True
                break
            g = old.get("geometry", {})
            if "p0" in g and not old["observation"]["needs_review"]:
                other = LineString([g["p0"], g["p1"]])
                if (
                    line.hausdorff_distance(other) < 1
                    and abs(old["height_m"] - wall["height_m"]) < 2
                ):
                    existing = True
                    break
        if not existing:
            program["walls"].append(wall)
        program["needs_review"] = all(w["observation"]["needs_review"] for w in program["walls"])
        program["review"] = dict(
            method="direct structure-only route survey; prior pilot retained",
            scope="matched walls only",
            independent=False,
        )
    save(directory / "validated-walls.json", accepted)
    save(directory / "rejections.json", rejected)
    save(directory / "programs.json", list(programs.values()))
    publish.write_text(json.dumps(list(programs.values()), indent=2) + "\n")
    for form in forms:
        row = rows[form["wall_id"]]
        event(
            root,
            wall_id=row["wall_id"],
            ring=row["ring"],
            batch=batch,
            status="rejected" if form.get("reject") else "done",
            reason=form.get("reason"),
            render_status="deferred"
            if form.get("render_deferred")
            else "rejected"
            if form.get("reject")
            else "eligible",
        )
    usage = snapshot()
    start = json.loads((directory / "start.json").read_text())
    timing = dict(
        accepted=len(accepted),
        rejected=len(rejected),
        quota=usage,
        quota_points=usage["used_percent"] - start["quota"]["used_percent"]
        if usage["used_percent"] is not None
        else None,
        elapsed_seconds=time.time() - start["time"],
    )
    timing["elapsed_seconds_per_wall"] = timing["elapsed_seconds"] / len(forms) if forms else None
    save(directory / "finished.json", timing)
    save(directory / "queue-state.json", statuses(root))
    print(json.dumps(timing))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["queue", "prepare", "compile"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--batch")
    p.add_argument("--count", type=int, default=12)
    p.add_argument("--publish", type=Path)
    p.add_argument("--alternate-from")
    p.add_argument("--wall-ids", nargs="+")
    p.add_argument("--source", choices=["google", "lookaround"])
    args = p.parse_args()
    if args.stage == "queue":
        make_queue(args.root)
    elif args.stage == "prepare":
        prepare(args.root, args.batch, args.count, args.alternate_from, args.wall_ids, args.source)
    else:
        compile_batch(args.root, args.batch, args.publish)


if __name__ == "__main__":
    main()
