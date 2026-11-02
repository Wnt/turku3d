from turku3d.coarse.horizon_identity import source_key


def test_relocated_identical_sources_reuse_the_render_product():
    old = [dict(tile='t1_0_0', buildings=['/old/a', '/old/b'], ground=['/old/c'])]
    new = [dict(tile='t1_0_0', buildings=['/new/b', '/new/a'], ground=['/new/c'])]
    hashes = {f'{root}/{name}': name * 64 for root in ('/old', '/new') for name in 'abc'}
    assert source_key('recipe', 't2_0_0', old, hashes) == source_key('recipe', 't2_0_0', new, hashes)


def test_content_role_and_coverage_changes_invalidate_identity():
    hashes = {'a': 'a' * 64, 'b': 'b' * 64}
    old = [dict(tile='t1_0_0', buildings=['a'], ground=['b'])]
    base = source_key('recipe', 't2_0_0', old, hashes)
    assert base != source_key('recipe', 't2_0_0', old, {**hashes, 'a': 'c' * 64})
    assert base != source_key('recipe', 't2_0_0', [dict(tile='t1_0_0', buildings=['b'], ground=['a'])], hashes)
    assert base != source_key('recipe', 't2_0_0', [*old, dict(tile='t1_1_0', buildings=[], ground=['b'])], hashes)
    assert base != source_key('changed', 't2_0_0', old, hashes)
