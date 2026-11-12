import argparse
from pathlib import Path
from .recipe import build_recipe

parser = argparse.ArgumentParser(description="Compile the bounded next-generation semantic pilot")
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--recipe", type=Path, required=True)
parser.add_argument("--asset-root", type=Path, required=True, help="Directory containing the recipe's content-addressed assets")
args = parser.parse_args()
scene = build_recipe(args.recipe,args.cache,args.asset_root,args.output)
print(f"{scene['id']}: {len(scene['meshes'])} meshes, {len(scene['supports'])} supports, {len(scene['collision'])} collision cells; {scene['release']}")
