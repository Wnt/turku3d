# Monocular metric depth on a GSV equirect via cubemap faces, compared with Google's plane depth.
import sys, time, numpy as np, torch, py360convert
from PIL import Image
from transformers import pipeline
Image.MAX_IMAGE_PIXELS = None
pid = sys.argv[1]; model = sys.argv[2] if len(sys.argv)>2 else "depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf"
torch.set_num_threads(10)
eq = np.asarray(Image.open(f"pano_{pid}_z5.jpg").resize((4096,2048), Image.LANCZOS))
F = 1024
faces = py360convert.e2c(eq, face_w=F, cube_format="dict")
pipe = pipeline("depth-estimation", model=model, device="cpu")
u = (np.arange(F)+0.5)/F*2-1
xx, yy = np.meshgrid(u, u)
raymul = np.sqrt(1+xx**2+yy**2)  # planar z -> ray distance for 90deg face
out = {}
for k in ["F","R","B","L"]:
    t=time.time()
    r = pipe(Image.fromarray(faces[k]))
    z = r["predicted_depth"].squeeze().numpy()
    z = np.array(Image.fromarray(z).resize((F,F), Image.BILINEAR))
    out[k] = (z*raymul).astype(np.float32)[...,None]
    print(k, "secs", round(time.time()-t,1), "z range", float(z.min()), float(z.max()))
for k in ["U","D"]: out[k] = np.zeros((F,F,1),np.float32)
mono = py360convert.c2e(out, h=256, w=512, cube_format="dict")[...,0]
np.save(f"mono_{pid}.npy", mono)
g = np.load(f"depth_{pid}.npy")
for flip in (False, True):
    gg = g[:, ::-1] if flip else g
    m = (gg>0)&(gg<60)&(mono>0.5)
    m[:20]=False; m[-60:]=False   # skip near-pole rows / car nadir
    ratio = gg[m]/mono[m]
    s = np.median(ratio)
    err = np.abs(mono[m]*s-gg[m])/gg[m]
    print(f"flip={flip} n={m.sum()} median scale(google/mono)={s:.3f} corr={np.corrcoef(gg[m],mono[m])[0,1]:.3f} AbsRel after scale={np.median(err):.3f} raw AbsRel={np.median(np.abs(mono[m]-gg[m])/gg[m]):.3f}")
vis = lambda d: (np.clip(1-d/60,0,1)*255).astype(np.uint8)
Image.fromarray(np.vstack([vis(np.where(g>0,g,999)), vis(mono)])).resize((1024,1024), Image.NEAREST).save(f"depth_compare_{pid}.png")
