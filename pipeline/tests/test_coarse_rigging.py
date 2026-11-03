"""Measured mast endpoints, source-shaped anchors and bounded rigging collision."""
import numpy as np
import shapely
from turku3d.coarse import rigging


def test_sailing_masts_taper_without_changing_measured_height_or_filling_the_rig():
    boat = dict(outline=shapely.Polygon([(-40,0),(-32,-7),(32,-7),(40,0),(32,7),(-32,7)]),
                deck=2.,masts=[(shapely.Point(x,0),h) for x,h in [(-22,30),(0,36),(22,28)]],tags={})
    parts=rigging.produce('measured',boat)
    colliding=[m for m in parts if m.metadata['collision']]
    assert len(colliding)==3
    assert sum(len(m.indices) for m in colliding) < 250
    for mast, (_,top) in zip(colliding,boat['masts'],strict=True):
        assert mast.positions[:,1].min()==2 and mast.positions[:,1].max()==top
        base=mast.positions[mast.positions[:,1]==2][:,[0,2]]
        tip=mast.positions[mast.positions[:,1]==top][:,[0,2]]
        assert np.ptp(base[:,0]) > 2*np.ptp(tip[:,0])
    assert all(np.isfinite(m.positions).all() for m in parts)
    assert sum(len(m.indices) for m in parts) < 6000
    assert len([m for m in parts if not m.metadata['collision']]) > 100


def test_rigging_anchors_follow_narrowing_hulls_and_non_sailing_masts_stay_slim():
    outline=shapely.Polygon([(-40,0),(-30,-7),(30,-7),(40,0),(30,7),(-30,7)])
    for distance in (-38,-20,0,20,38):
        for side in (-1,1):
            p=rigging.anchor(outline,np.zeros(2),np.array([1,0]),np.array([0,1]),distance,side,14)
            assert outline.covers(shapely.Point(p))
    assert rigging.anchor(outline,np.zeros(2),np.array([1,0]),np.array([0,1]),45,1,14) is None
    parts=rigging.produce('radio',dict(outline=outline,deck=2,masts=[(shapely.Point(0,0),18)],tags={}))
    assert len(parts)==1 and np.ptp(parts[0].positions[:,0]) <= .11+1e-6
