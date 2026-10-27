from streetlevel import lookaround
import inspect
print([n for n in dir(lookaround) if not n.startswith('_')])
panos = lookaround.get_coverage_tile_by_latlon(60.4506, 22.2642).panos
p = panos[0]
auth = lookaround.Authenticator()
faces=[]
for face in range(6):
    b = lookaround.get_panorama_face(p, face, 0, auth)
    open(f"la_{p.id}_f{face}.heic","wb").write(b); faces.append(len(b))
print("face bytes", faces)
try:
    from pillow_heif import register_heif_opener; register_heif_opener()
    from PIL import Image
    for f in range(6):
        im = Image.open(f"la_{p.id}_f{f}.heic"); print(f, im.size); im.convert("RGB").save(f"la_f{f}.jpg", quality=85)
    print(inspect.signature(lookaround.to_equirectangular))
except Exception as e: print("decode:", repr(e))
