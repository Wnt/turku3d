# blender -b -P render_curb.py -- <glb> <out.png> <cx> <cy>
# Close-up oblique render of a curb location, flat grey material + sun, workbench engine.
import sys, math, bpy
from mathutils import Vector

glb, out, cx, cy = sys.argv[sys.argv.index('--') + 1:][:4]
cx, cy = float(cx), float(cy)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
obj = [o for o in bpy.context.scene.objects if o.type == 'MESH'][0]
# ground height near the target
zs = [v.co.z for v in obj.data.vertices if abs(v.co.x - cx) < 2 and abs(v.co.y - cy) < 2]
cz = sum(zs) / len(zs)
sc = bpy.context.scene
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
sc.collection.objects.link(cam)
cam.location = Vector((cx + 3.2, cy - 3.2, cz + 1.6))
d = Vector((cx, cy, cz)) - cam.location
cam.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
cam.data.lens = 28
sc.camera = cam
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'STUDIO'
sc.display.shading.show_cavity = True
sc.display.shading.show_object_outline = False
sc.display.shading.color_type = 'SINGLE'
sc.display.shading.single_color = (0.75, 0.75, 0.72)
sc.display.shading.show_xray = False
bpy.context.scene.display.shading.show_specular_highlight = True
sc.render.resolution_x, sc.render.resolution_y = 900, 560
sc.render.filepath = out
# wireframe overlay via modifier copy
w = obj.copy(); w.data = obj.data.copy(); sc.collection.objects.link(w)
mod = w.modifiers.new('wf', 'WIREFRAME'); mod.thickness = 0.008; mod.use_replace = True
w.color = (0.1, 0.1, 0.1, 1)
sc.display.shading.color_type = 'OBJECT'
obj.color = (0.78, 0.78, 0.74, 1)
bpy.ops.render.render(write_still=True)
print('rendered', out, 'z', round(cz, 2))
