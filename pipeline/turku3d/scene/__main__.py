import argparse
from pathlib import Path
from .compile import compile_scene, write_products

parser = argparse.ArgumentParser(description="Compile the bounded next-generation semantic pilot")
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
scene = write_products(compile_scene(args.cache), args.output)
print(f"{scene['id']}: {len(scene['meshes'])} meshes, {len(scene['supports'])} supports, {len(scene['collision'])} collision cells; {scene['release']}")
