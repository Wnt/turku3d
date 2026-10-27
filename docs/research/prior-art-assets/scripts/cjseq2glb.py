import sys, json, numpy as np, trimesh
from collections import Counter
src,dst,lod = sys.argv[1], sys.argv[2], sys.argv[3]
lines=open(src).read().splitlines()
meta=json.loads(lines[0]); tf=meta["transform"]; sc=np.array(tf["scale"]); tr=np.array(tf["translate"])
meshes=[]; stats=Counter(); faces_total=0
for ln in lines[1:]:
    o=json.loads(ln); lt=o.get("transform",tf)
    V=np.array(o["vertices"])*np.array(lt["scale"])+np.array(lt["translate"])
    for cid,co in o["CityObjects"].items():
        stats[co["type"]]+=1
        for g in co.get("geometry",[]):
            if str(g["lod"])!=lod: continue
            sem=g.get("semantics",{}); 
            shells=g["boundaries"] if g["type"]=="Solid" else [g["boundaries"]]
            for shell in shells:
                for face in shell:
                    ring=face[0]
                    if len(ring)<3: continue
                    pts=V[ring]
                    for i in range(1,len(ring)-1):
                        meshes.append([pts[0],pts[i],pts[i+1]])
                    faces_total+=1
            for s in sem.get("surfaces",[]): stats["sem_"+s["type"]]+=1
tri=np.array(meshes); c=tri.reshape(-1,3).mean(0)
v=(tri.reshape(-1,3)-c); v=v[:,[0,2,1]]*[1,1,-1]  # z-up -> y-up
m=trimesh.Trimesh(vertices=v, faces=np.arange(len(v)).reshape(-1,3), process=True)
m.export(dst)
print(dict(stats), "polygons", faces_total, "tris", len(m.faces), "verts", len(m.vertices), "center", c.round(2))
