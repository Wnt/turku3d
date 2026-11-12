import argparse
import json
from pathlib import Path
from .compile import compile_scene, write_products

parser = argparse.ArgumentParser(description="Compile the bounded next-generation semantic pilot")
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--assets", type=Path, help="Frozen approved asset records from source adapter")
parser.add_argument("--materials", type=Path, help="Frozen published PBR material recipes")
parser.add_argument("--trees", type=Path, help="Prepared supported tree instances")
args = parser.parse_args()
scene = write_products(compile_scene(args.cache, assets=json.loads(args.assets.read_text()) if args.assets else [], material_recipes=json.loads(args.materials.read_text()) if args.materials else [], trees=json.loads(args.trees.read_text()) if args.trees else []), args.output)
print(f"{scene['id']}: {len(scene['meshes'])} meshes, {len(scene['supports'])} supports, {len(scene['collision'])} collision cells; {scene['release']}")
