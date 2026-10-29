"""Read direct stage code identities without building tiles or reading raw inputs.

Run with each frozen source's pipeline on PYTHONPATH. Values are the source_key
expressions used by that source, not an estimate from changed filenames. Upstream
output/data changes can additionally invalidate downstream content keys.
"""
import ast
import importlib
import inspect
import json
from pathlib import Path

from turku3d import config as C
from turku3d.stage_cache import content_key


class SourceText(str):
    pass


def identities(narrow_samplers=False):
    result = {}
    for name in ('coarse.build', 'coarse.road_top', 'coarse.banks', 'coarse.stairs',
                 'coarse.parked', 'coarse.packaging', 'wall_assembly'):
        try:
            module = importlib.import_module('turku3d.' + name)
        except ModuleNotFoundError as error:
            if error.name == 'turku3d.' + name:
                continue  # A newer stage may not exist in an older deployment.
            raise
        path = Path(module.__file__)
        source = path.read_text()
        tree = ast.parse(source)
        for function in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            env = {**vars(module), 'base': C.REPO_ROOT / 'pipeline/turku3d',
                   'common': [Path(C.__file__), C.REPO_ROOT / 'pipeline/turku3d/col/materials_v1.json']}
            for node in ast.walk(function):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
                if isinstance(node, ast.FunctionDef) and node is not function:
                    env[node.name] = SourceText(ast.get_source_segment(source, node))
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'dependencies' for t in node.targets):
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
            def key(*args):
                values = []
                for value in args:
                    if (narrow_samplers and name in ('coarse.build', 'coarse.road_top', 'coarse.parked')
                            and isinstance(value, Path) and value.name == 'surfaces.py'):
                        value = importlib.import_module('turku3d.coarse.surfaces').grid_height
                    values.append(str(value) if isinstance(value, SourceText) else value.read_bytes()
                                  if isinstance(value, Path) else inspect.getsource(value))
                return content_key(values)
            env['source_key'] = key
            for call in ast.walk(function):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == 'cache'
                        and call.func.attr in ('run', 'files') and len(call.args) >= 2):
                    continue
                key = call.args[1]
                if not (isinstance(key, ast.Call) and isinstance(key.func, ast.Name)
                        and key.func.id == 'source_key'):
                    continue
                stage = call.args[0].value if isinstance(call.args[0], ast.Constant) else 'collision_*'
                result[stage] = eval(compile(ast.Expression(key), str(path), 'eval'), env)
    from turku3d.coarse.inputs import _key, DEPENDENCIES
    for dataset in ['ground', 'ortho', *DEPENDENCIES]:
        result['input/' + dataset] = _key([], dataset)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--narrow-samplers', action='store_true')
    args = parser.parse_args()
    print(json.dumps(identities(args.narrow_samplers), sort_keys=True))
