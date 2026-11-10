from test_rain_repair_endpoints import module


def test_only_accounting_differences_allowed(monkeypatch):
    mod = module(monkeypatch, 'finalize_pipeline_cleanup')
    old = {'device': 58, 'inode': 1, 'identity': [1, 2, 3], 'bytes': 2, 'links': 1,
           'allocated_bytes': 512, 'path': '/retained/file'}
    assert mod.stable_record_equal(old, dict(old, device=67))
    assert mod.stable_record_equal(old, dict(old, allocated_bytes=4096))
    assert mod.stable_record_equal(old, dict(old, device=67, allocated_bytes=4096))
    for key, value in [('inode', 9), ('identity', [1, 2, 4]), ('bytes', 3), ('links', 2),
                       ('path', '/different/file')]:
        assert not mod.stable_record_equal(old, dict(old, **{key: value}))
