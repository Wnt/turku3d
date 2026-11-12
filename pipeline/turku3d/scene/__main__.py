import argparse
import json
from pathlib import Path
import sys
import time
from .recipe import build_recipe

parser = argparse.ArgumentParser(description="Compile the bounded next-generation semantic pilot")
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--recipe", type=Path, required=True)
parser.add_argument("--asset-root", type=Path, required=True, help="Directory containing the recipe's content-addressed assets")
args = parser.parse_args()
started = time.perf_counter()
timings = {}
scene = build_recipe(args.recipe,args.cache,args.asset_root,args.output,timings)
timings['totalMs'] = round((time.perf_counter() - started) * 1000)
print(json.dumps(dict(scene=scene['id'], timings=timings)), file=sys.stderr)
print(f"{scene['id']}: {len(scene['meshes'])} meshes, {len(scene['supports'])} supports, {len(scene['collision'])} collision cells; {scene['release']}")
