"""Failure paths, data identity and calibration must not create false green runs."""
import importlib
import json
import threading
import time
import numpy as np
import pytest
from PIL import Image
from qalib import registry
from qalib.paths import resolve, rel
from qalib.provenance import view_digest
from regress import compare
from calibrate import summarize
from judge.batch import judge_pairs
from judge.judge import QuotaError
from report.backmap import annotate
from views.heading import solve_heading
from views.refine import match_pose


def row(run, scores=None, **kw):
    return dict(run_id=run, scores=scores or {'a': 6.,'b': 8.}, kind='render', complete=True,
                commit='abc', comparison_key='same', **kw)


def test_regression_boundary_and_incomparable_runs():
    base=row('baseline')
    assert compare(base,row('equal',{'a':5.,'b':8.}),.5)['ok']
    assert not compare(base,row('drop',{'a':4.9,'b':8.}),.5)['ok']
    for bad in [dict(row('x'), complete=False),dict(row('x'), scores={'a':9}),
                dict(row('x'), comparison_key='new-photo'),dict(row('x'), commit=None),
                dict(row('x'), scores={'a':float('nan'),'b':8})]:
        with pytest.raises(ValueError): compare(base,bad)
    for margin in [-1,float('inf'),float('nan')]:
        with pytest.raises(ValueError):compare(base,row('x'),margin)


def test_calibration_sample_variance():
    assert summarize([{'ok':True,'result':{'similarity':x}} for x in [5,6,7]])['sample_variance']==1
    with pytest.raises(ValueError):summarize([{'ok':False}]*3)


def test_parallel_bounded_and_quota_stop(tmp_path):
    pairs=[dict(pair_id=str(i),ref='x',render='y') for i in range(8)]
    lock=threading.Lock();active=0;peak=0
    def fake(*args,**kw):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.03)
        with lock:active-=1
        return {'ok':True,'result':{'similarity':5}}
    assert len(judge_pairs(pairs,tmp_path,workers=3,judge_fn=fake))==8
    assert peak==3
    calls=[]
    def quota(*args,**kw):
        calls.append(1);raise QuotaError('100%')
    result=judge_pairs(pairs,tmp_path/'quota',workers=1,judge_fn=quota)
    assert len(calls)==1 and len(result)==1 and result['0']['quota']


def test_paths_and_photo_identity(tmp_path,monkeypatch):
    monkeypatch.setenv('QA_DATA',str(tmp_path))
    p=tmp_path/'refs/a.png';p.parent.mkdir();Image.new('RGB',(16,16),'red').save(p)
    assert resolve('data/qa/refs/a.png')==p and rel(p)=='data/qa/refs/a.png'
    view=dict(registry.load()[0],image=rel(p))
    before=view_digest(view);Image.new('RGB',(16,16),'blue').save(p)
    assert before!=view_digest(view)
    assert view_digest(view)!=view_digest(dict(view,pose=dict(view['pose'],yaw=30)))


def test_missing_asset_does_not_claim_background_id(tmp_path):
    png=tmp_path/'id.png';table=tmp_path/'id.json'
    Image.new('RGB',(10,10),(0,0,1)).save(png)
    table.write_text(json.dumps({'1':{'id':'ground','tile':'t0_0_0'}}))
    issues=[{'bbox':[0,0,1,1],'category':'missing_object'}]
    annotate(issues,png,table)
    assert issues[0]['objects']==[] and issues[0]['overlapping_objects'][0]['id']=='ground'
    assert issues[0]['mapping_status']=='missing_asset_requires_spatial_lookup'


def test_missing_echo_and_failed_pass_not_accepted(tmp_path):
    from loop import validate_render
    v=registry.load()[0]
    meta={'pose':v['pose'],'width':v['width'],'height':v['height'],'time':v.get('time'),
          'passes':{'color':{'error':'failed','ready':True}}}
    (tmp_path/f"{v['id']}.meta.json").write_text(json.dumps(meta))
    Image.new('RGB',(v['width'],v['height'])).save(tmp_path/f"{v['id']}.png")
    with pytest.raises(ValueError,match='pass failed'):validate_render(v,tmp_path)


def test_heading_wrap_and_weak_registration_rejected():
    result=solve_heading(0,0,[{'E':0,'N':10,'tour_yaw':1},{'E':10,'N':0,'tour_yaw':91}])
    assert abs(result['heading_offset_grid_deg']+1)<1e-8
    empty=np.zeros((64,64),np.uint8)
    assert not match_pose(empty,empty,np.full((64,64,3),np.nan),registry.load()[0]['pose'])['accepted']


def test_judge_nonzero_exit_cannot_reuse_valid_json(tmp_path,monkeypatch):
    j=importlib.import_module('judge.judge')
    monkeypatch.setattr(j,'usage_percent',lambda:2)
    photo=tmp_path/'a.jpg';Image.new('RGB',(16,16)).save(photo)
    def fake(cmd,**kw):
        from types import SimpleNamespace
        Path=__import__('pathlib').Path
        Path(cmd[cmd.index('-o')+1]).write_text(json.dumps(dict(same_place='yes',similarity=10,summary='x',issues=[],ignored_differences=[])))
        return SimpleNamespace(returncode=1,stdout='',stderr='failed')
    monkeypatch.setattr(j.subprocess,'run',fake)
    result=j.judge(photo,photo,retries=0)
    assert not result['ok']


def test_acceptance_blocks_ground_only_package(tmp_path):
    from acceptance import check
    table=tmp_path/'ids.json';table.write_text(json.dumps({'1':{'kind':'ground','id':'ground'}}))
    views=registry.load()
    run={'complete':True,'expected_views':[v['id'] for v in views], 'manifest_sha256':'abc',
         'comparison_key':'same','pairs':[{'id_table':str(table)}]}
    errors=check(run,views,{'n':3,'comparison_key':'same'})
    assert errors==['current package exposes no building asset IDs; assembled building layer is required']


def test_loop_rejects_missing_render_and_keeps_report(tmp_path,monkeypatch):
    from loop import main
    monkeypatch.setenv('QA_DATA',str(tmp_path))
    p=tmp_path/'refs/a.png';p.parent.mkdir();Image.new('RGB',(16,16)).save(p)
    view=dict(registry.load()[0],image=str(p),width=16,height=16)
    reg=tmp_path/'one.jsonl';reg.write_text(json.dumps(view)+'\n')
    renders=tmp_path/'renders';renders.mkdir()
    assert main(['--views',str(reg),'--renders',str(renders),'--run-id','missing','--no-judge'])==1
    run=json.loads((tmp_path/'runs/missing/run.json').read_text())
    assert not run['complete'] and run['errors']
    assert (tmp_path/'runs/missing/report.html').is_file()
