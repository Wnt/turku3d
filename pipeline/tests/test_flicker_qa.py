import numpy as np
from turku3d.flicker_qa import analyze


def sequence():
    frames = [dict(file=f'{group}-{i}',group=group,products=[dict(id='tile',hash='a')],meshes=[])
              for group in ['still','micro-left','micro-right'] for i in range(4)]
    return dict(frames=frames), [np.full((20,30,3),80,dtype=np.uint8) for _ in frames]


def test_stable_is_clean():
    d,im = sequence();r,_,_=analyze(d,im)
    assert r['unstable_pixels']==0
    assert r['classification']==['no_instability_above_threshold']


def test_interior_flicker_and_product_swap_are_reported():
    d,im=sequence();im[2][5:10,5:10]=160
    d['frames'][2]['products'][0]['hash']='b'
    r,_,_=analyze(d,im)
    assert r['still_unstable_pixels']==25
    assert r['interior_unstable_pixels']==25
    assert 'suspected_z_fighting_or_shader_instability' in r['classification']
    assert 'product_or_mesh_swap' in r['classification']


def test_subpixel_edge_motion_is_excluded():
    d,im=sequence()
    for n,x in enumerate(im):
        x[:,15:]=200
        if n>=4:x[:,14]=200
    r,_,_=analyze(d,im)
    assert r['micro_residual_pixels']==0
    assert r['unstable_pixels']==0
