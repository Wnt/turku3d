"""Analyze capture-loop --flicker sequences without optical-flow dependencies.

Repeated identical camera poses measure temporal instability directly. A one-pixel
colour envelope excludes micro-step differences explainable by small image motion.
Labels are suspects, not a substitute for depth/material inspection or AA-off A/B.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import numpy as np
from PIL import Image


def envelope(image):
    h, w = image.shape[:2]
    p = np.pad(image, ((1,1),(1,1),(0,0)), mode='edge')
    lo, hi = image.copy(), image.copy()
    for y in range(3):
        for x in range(3):
            np.minimum(lo, p[y:y+h,x:x+w], out=lo)
            np.maximum(hi, p[y:y+h,x:x+w], out=hi)
    return lo, hi


def analyze(data, images, threshold=8):
    groups = defaultdict(list)
    for frame, image in zip(data['frames'], images, strict=True):
        groups[frame['group']].append(image.astype(np.float32))
    if any(len(groups.get(k, [])) < 4 for k in ['still','micro-left','micro-right']):
        raise ValueError('need at least four samples at each of still/micro-left/micro-right')
    means = {k:np.mean(v, axis=0) for k,v in groups.items()}
    # Range rather than variance retains brief alternating or one-frame defects.
    temporal = {k:np.ptp(np.stack(v),axis=0).max(2) for k,v in groups.items()}
    lo, hi = envelope(means['still'])
    micro = np.maximum.reduce([np.maximum(lo-v, v-hi).clip(0).max(2) for k,v in means.items() if k!='still'])
    instability = np.maximum.reduce(list(temporal.values()))
    edges = (hi-lo).max(2) > 40
    still_mask = temporal['still'] > threshold
    micro_mask = micro > threshold
    unstable = (instability > threshold) | micro_mask
    interior = unstable & ~edges
    changes = []
    for previous, current in zip(data['frames'], data['frames'][1:]):
        products_a = {p['id']:p for p in previous.get('products',[]) if 'model' not in p or p['model']}
        products_b = {p['id']:p for p in current.get('products',[]) if 'model' not in p or p['model']}
        mesh_a = {p['uuid']:p for p in previous.get('meshes',[])}
        mesh_b = {p['uuid']:p for p in current.get('meshes',[])}
        product_ids = [k for k in products_a.keys() | products_b.keys() if products_a.get(k)!=products_b.get(k)]
        mesh_ids = [k for k in mesh_a.keys() | mesh_b.keys() if mesh_a.get(k)!=mesh_b.get(k)]
        if product_ids or mesh_ids:
            changes.append(dict(frame=current['file'], products=sorted(product_ids), meshes=sorted(mesh_ids)))
    pixels = unstable.size
    labels = []
    if changes:
        labels.append('product_or_mesh_swap')
    if interior.sum() >= max(8, pixels*.00005):
        labels.append('suspected_z_fighting_or_shader_instability')
    if (unstable & edges).sum() > 0:
        labels.append('suspected_taa_or_edge_shimmer')
    if not labels:
        labels.append('no_instability_above_threshold')
    report = dict(classification=labels, threshold_8bit=threshold, frames=len(images),
                  still_unstable_pixels=int(still_mask.sum()), micro_residual_pixels=int(micro_mask.sum()),
                  unstable_pixels=int(unstable.sum()), interior_unstable_pixels=int(interior.sum()),
                  unstable_fraction=float(unstable.mean()), changes=changes,
                  limitations=['heuristic labels; AA-off/depth inspection needed for causality',
                               'one-pixel motion envelope; parallax/animated shaders may remain',
                               'micro steps are not a substitute for the full driving route'])
    if all('owners' in f for f in data['frames']):
        report['surface_overlap_frames'] = sum(any(o['primary'] and o['scenery'] for o in f['owners']) for f in data['frames'])
        report['full_pass_frames'] = sum(any(o.get('phase') == 'full' and o['primary'] for o in f['owners']) for f in data['frames'])
        previous_phase, reversals = {}, []
        for frame in data['frames']:
            for owner in frame['owners']:
                phase = owner.get('phase')
                if phase not in ('preview', 'first', 'full'):
                    continue
                if previous_phase.get(owner['id']) == 'full' and phase != 'full':
                    reversals.append(dict(frame=frame['file'], tile=owner['id'], phase=phase))
                previous_phase[owner['id']] = phase
        report['pass_reversals'] = reversals
    strength = np.maximum(instability, micro)
    return report, means['still'], strength


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sequence', type=Path)
    parser.add_argument('--threshold',type=float,default=8)
    args = parser.parse_args()
    data = json.loads((args.sequence/'frames.json').read_text())
    if data.get('failed_assets'):
        raise ValueError('sequence has asset failures')
    images = [np.asarray(Image.open(args.sequence/f['file']).convert('RGB')) for f in data['frames']]
    if any(i.shape != (720,1280,3) for i in images):
        raise ValueError('expected 1280x720 PNG frames')
    report, base, strength = analyze(data, images,args.threshold)
    report.update(pose=data.get('pose'),micro_step_m=data.get('micro_step_m'))
    (args.sequence/'flicker.json').write_text(json.dumps(report,indent=1)+'\n')
    alpha = np.clip(strength / max(args.threshold,1),0,1)[...,None]*.75
    heat = np.zeros_like(base);heat[:,:,0]=255;heat[:,:,1]=np.clip(strength*2,0,160)
    Image.fromarray(np.uint8(base*(1-alpha)+heat*alpha)).save(args.sequence/'flicker-overlay.png')
    Image.fromarray(np.uint8(np.clip(strength*4,0,255))).save(args.sequence/'flicker-heatmap.png')
    print('flicker-qa '+json.dumps({k:v for k,v in report.items() if k not in ['changes','limitations','pose']}))


if __name__=='__main__':
    main()
