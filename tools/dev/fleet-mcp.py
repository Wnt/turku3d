#!/usr/bin/env python3
"""Fleet workflow client. Every Blender operation is a parameterised MCP tool call.

uvx --from blenderwright@3.1.0 python tools/dev/fleet-mcp.py --port 9878 --inspect
uvx --from blenderwright@3.1.0 python tools/dev/fleet-mcp.py --port 9878 --recipe operations.json
uvx --from blenderwright@3.1.0 python tools/dev/fleet-mcp.py --port 9878 --build model.json

This client never imports bpy, executes Blender Python, or connects to the add-on socket.
The ordinary Blenderwright stdio MCP server handles its own existing per-port connection.
"""
import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
import struct
import subprocess
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def triangle_count(path):
    blob = Path(path).read_bytes()
    size, kind = struct.unpack_from('<II', blob, 12)
    if blob[:4] != b'glTF' or kind != 0x4E4F534A:
        raise ValueError(f'Not a binary glTF: {path}')
    data = json.loads(blob[20:20+size])
    return sum(data['accessors'][p.get('indices', p['attributes']['POSITION'])]['count']//3
               for mesh in data.get('meshes', []) for p in mesh['primitives'] if p.get('mode', 4) == 4)


def asset_contract(path, wheels):
    blob = Path(path).read_bytes()
    size = struct.unpack_from('<I', blob, 12)[0]
    data = json.loads(blob[20:20+size])
    nodes = data.get('nodes', [])
    for name, expected in wheels.items():
        matches = [n for n in nodes if n.get('name') == name]
        if len(matches) != 1:
            raise ValueError(f'{path}: expected one {name} node, got {len(matches)}')
        actual = matches[0].get('translation', [0, 0, 0])
        if max(abs(a-b) for a, b in zip(actual, expected)) > .001:
            raise ValueError(f'{name}: exported hub {actual} differs from {expected}')
    names = [m.get('name', '') for m in data.get('materials', [])]
    lamp_names = {'lamp-front-left', 'lamp-front-right', 'lamp-tail-left', 'lamp-tail-right'}
    if not names or any(not n.startswith('fleet_') and n not in lamp_names for n in names):
        raise ValueError(f'Unexpected material roles: {names}')
    if not lamp_names.issubset(names):
        raise ValueError('Four named front/tail lamp materials are required')
    if data.get('images'):
        raise ValueError('Reference textures must never enter fleet exports')
    return names


async def main(args):
    started = time.monotonic()
    server = StdioServerParameters(command='/opt/homebrew/bin/uvx', args=['blenderwright@3.1.0'],
                                  env={**os.environ, 'BLENDERWRIGHT_PORT': str(args.port)})
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool, **arguments):
                if tool in {'execute_blender_code', 'get_viewport_screenshot'}:
                    raise ValueError(f'Forbidden tool: {tool}')
                result = await session.call_tool(tool, arguments)
                if result.isError:
                    raise RuntimeError(f'{tool}: {result.content}')
                data = result.structuredContent
                if data is None:
                    data = json.loads(next(c.text for c in result.content if c.type == 'text'))
                return data

            if args.inspect:
                info = await call('get_scene_info')
                print(json.dumps({'scene': info['scene_name'], 'objects': info['object_count']}))
                return
            if args.recipe:
                operations = json.loads(Path(args.recipe).read_text())
                operations = operations.get('operations', []) if isinstance(operations, dict) else operations
                for i, op in enumerate(operations):
                    await call(op['tool'], **op['args'])
                    print(f'{i+1}/{len(operations)} {op["tool"]}', flush=True)
                return

            model = json.loads(Path(args.build).read_text())
            source = Path(model['blend']).expanduser().resolve()
            output = Path(model['output']).expanduser().resolve()
            output.mkdir(parents=True, exist_ok=True)
            wheels = model['wheels']['pivots']
            if set(wheels) != {'wheel_FL', 'wheel_FR', 'wheel_RL', 'wheel_RR'}:
                raise ValueError('Four named wheel pivots are required')
            await call('open_file', filepath=str(source))
            # References and Boolean cutters are hidden and never enter the export.
            scene = await call('get_scene_info')
            originals = [o['name'] for o in scene['objects'] if o['type'] == 'MESH' and o['visible']
                         and not o['name'].startswith(('reference_', 'arch_'))]
            if not originals:
                raise ValueError('No visible model meshes')
            if not set(wheels).issubset(originals):
                raise ValueError('Source blend must contain four separate named wheel meshes')
            for view in model.get('views', ['side', 'front', 'rear', 'three-quarter']):
                await call('set_active_camera', object_name=f'compare_{view}')
                await call('capture_viewport', filepath=str(output/f'{view}.png'), width=1280, height=720)
            for original in originals:
                await call('rename_object', old_name=original, new_name=f'SOURCE_{original}')
                await call('set_object_visibility', object_name=f'SOURCE_{original}', visible=False)
            counts = []
            for lod, budget in enumerate(model.get('triangle_budgets', [40000, 10000, 2000])):
                copies, wheel_copies = [], []
                for original in originals:
                    if lod == 2 and original.startswith(('handle_', 'wiper_', 'door_gap_', 'tailgate_gap', 'plate_text_', 'fastener_', 'grille_slat_', 'rail_foot_')):
                        continue
                    duplicate = await call('duplicate_object', object_name=f'SOURCE_{original}', linked=False)
                    name = duplicate['name']
                    await call('set_object_visibility', object_name=name, visible=True)
                    info = await call('get_object_info', object_name=name)
                    for modifier in info['modifiers']:
                        if modifier['type'] == 'SUBSURF':
                            for prop in ['levels', 'render_levels']:
                                await call('set_modifier_property', object_name=name, modifier_name=modifier['name'],
                                           property=prop, value=2)
                        elif lod == 2 and modifier['type'] == 'BEVEL':
                            await call('set_modifier_property', object_name=name, modifier_name=modifier['name'], property='segments', value=1)
                    await call('convert_object', object_name=name, target='MESH')
                    if original in wheels:
                        await call('rename_object', old_name=name, new_name=original)
                        await call('apply_transforms', object_name=original, location=False, rotation=True, scale=True)
                        wheel_copies.append(original)
                    else:
                        copies.append(name)
                await call('join_objects', names=copies)
                name = 'body'
                await call('rename_object', old_name=copies[0], new_name=name)
                exported = [name, *wheel_copies]
                await call('select_objects', names=exported, deselect_others=True)
                file = output/f'lod{lod}.glb'
                await call('export_file', filepath=str(file), selected_only=True,
                           options={'export_apply': True, 'export_animations': False})
                triangles = triangle_count(file)
                if triangles > budget:
                    for part in exported:
                        await call('decimate_mesh', object_name=part, ratio=.95*budget/triangles)
                    await call('select_objects', names=exported, deselect_others=True)
                    await call('export_file', filepath=str(file), selected_only=True,
                               options={'export_apply': True, 'export_animations': False})
                    triangles = triangle_count(file)
                if triangles > budget:
                    raise ValueError(f'LOD{lod} exceeds budget: {triangles}>{budget}')
                for part in exported:
                    await call('smart_uv_project', object_name=part, island_margin=.01)
                if lod == 0 and model.get('bake'):
                    await call('bake_texture', object_name=name, filepath=str(output/'surface.png'), **model['bake'])
                await call('select_objects', names=exported, deselect_others=True)
                await call('export_file', filepath=str(file), selected_only=True,
                           options={'export_apply': True, 'export_animations': False})
                materials = asset_contract(file, wheels)
                counts.append(triangles)
                # Inspect the actual LOD, using the same comparison camera and lighting.
                await call('set_active_camera', object_name='compare_three-quarter')
                await call('capture_viewport', filepath=str(output/f'lod{lod}.png'), width=1280, height=720)
                for part in exported:
                    await call('set_object_visibility', object_name=part, visible=False)
                    await call('rename_object', old_name=part, new_name=f'BUILT_LOD{lod}_{part}')
                print(json.dumps({'lod': lod, 'triangles': triangles, 'file': str(file)}), flush=True)
            await call('save_file', filepath=str(output/'export.blend'))
            record = {'id': model['id'], 'source': str(source), 'dimensions': model['dimensions'], 'wheels': model['wheels'],
                      'materials': materials, 'spec': model['spec'],
                      'triangles': counts, 'build_minutes': (time.monotonic()-started)/60,
                      'model_started_at': model.get('model_started_at'), 'judged': False}
            (output/'registry-entry.json').write_text(json.dumps(record, indent=2)+'\n')
            repo = Path(__file__).resolve().parents[2]
            subprocess.run(['node', str(repo/'game/scripts/prepare-fleet.mjs'), str(output),
                            str(Path(model['destination']).expanduser()), '--articulated'], check=True)
            # Runtime registry is a per-model file: concurrent modellers never rewrite a shared list.
            destination = Path(model['destination']).expanduser()
            (destination/'spec.json').write_text(json.dumps(model['spec'], indent=2)+'\n')
            registry = {**model['registry'], 'id': model['id'], 'wheels': model['wheels'],
                        'triangles': counts, 'spec': model['spec'], 'accepted': False}
            registry_path = repo/'game/src/actors/fleet/models'/f'{model["id"]}.json'
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps(registry, indent=2)+'\n')
            if not args.skip_game_capture:
                capture = model['capture']
                pair = output/f'pair-{int(time.time())}'
                pair.mkdir()
                # A fresh module graph lets a resident city pick up new assets without reloading tiles.
                preview = repo/'game/src/actors'/f'fleet-preview-{model["id"]}-{int(time.time())}'
                shutil.copytree(repo/'game/src/actors/fleet', preview)
                try:
                    module = f'/src/actors/{preview.name}'
                    baseline = capture.get('baselineModel', 'fleet-estate-3')
                    for label, target in [('before', baseline), ('after', model['id'])]:
                        setup = pair/f'{label}-setup.js'
                        setup.write_text(f'''(async () => {{
  const qa = await import({json.dumps(module+'/qa.ts')});
  const catalog = await import({json.dumps(module+'/catalog.ts')});
  const game = window.__game;
  const deadline = performance.now() + 60000;
  while (!window.__qaReady || !game.lights.source.host.manifest.vehicles?.length) {{
    if (performance.now() > deadline) throw Error('Native parked records did not become ready');
    await new Promise(requestAnimationFrame);
  }}
  const pairKey = {json.dumps(module)};
  if (game.fleetPairKey !== pairKey) {{
    game.fleetPairManifest = structuredClone(game.lights.source.host.manifest);
    game.fleetPairKey = pairKey;
  }}
  const manifest = structuredClone(game.fleetPairManifest);
  // Resolve identical native records for both views; replace only the selected body model.
  manifest.vehicles = (manifest.vehicles ?? []).map(car => {{
    if (car.model === 'truck') return car;
    const resolved = catalog.fleetModel(car.model, car.object_id, false).id;
    return {{...car, model: resolved === {json.dumps(baseline)} ? {json.dumps(target)} : resolved}};
  }});
  await qa.installFleetQA(undefined, manifest);
  if (!game.fleetQA.snapshot().instances) throw Error('Fleet pair has no visible instances');
  // Let the real render loop compile the new fleet material before the capture settle gate.
  for (let i=0;i<12;i++) await new Promise(requestAnimationFrame);
}})()''')
                        subprocess.run(['node', str(repo/'tools/dev/capture-loop.mjs'), '--cdp', capture['cdp'],
                                        '--base', capture['base'], '--cameras', capture['cameras'], '--only', capture['pose'],
                                        '--at', capture.get('at', '13:00'), '--quality', 'medium',
                                        '--guard-owner', capture.get('owner', 'cars'), '--out', str(pair/label),
                                        '--setup-file', str(setup)], check=True)
                    receipts = [json.loads((pair/label/'shots.json').read_text())['shots'][0]['capture']
                                for label in ['before', 'after']]
                    for key in ['effective_pose', 'effective_hour', 'quality', 'backend', 'tiles']:
                        if receipts[0][key] != receipts[1][key]:
                            raise ValueError(f'Unmatched capture pair: {key}')
                    if receipts[0]['quality'] != 'medium':
                        raise ValueError('Fleet acceptance capture requires medium quality')
                    record['pair'] = str(pair)
                finally:
                    shutil.rmtree(preview)
            record['game_capture'] = not args.skip_game_capture
            (output/'registry-entry.json').write_text(json.dumps(record, indent=2)+'\n')
            print(json.dumps(record))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9878)
    parser.add_argument('--skip-game-capture', action='store_true', help='Export-only iteration; not end-to-end acceptance')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--inspect', action='store_true')
    group.add_argument('--recipe')
    group.add_argument('--build')
    asyncio.run(main(parser.parse_args()))
