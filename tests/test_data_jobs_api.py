import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from stocklab import storage
from stocklab.app import app
from stocklab.data import download, finmind, import_csv, validate_csv
from stocklab.models import DownloadRequest, ImportRequest, JobRequest, Rule, Schedule, Strategy
from stocklab.worker import run, scheduled
from tests.conftest import bars


def imported():
    return import_csv(ImportRequest(symbol="TEST", csv_text=bars(range(10, 110)).to_csv(index=False)))


@pytest.mark.parametrize("change", ["duplicate", "unsorted", "nan", "bad_ohlc", "future", "negative_volume"])
def test_quality_rejects_bad_input(change):
    f = bars([10, 11, 12])
    if change == "duplicate": f.loc[1, "date"] = f.loc[0, "date"]
    if change == "unsorted": f = f.iloc[::-1]
    if change == "nan": f.loc[1, "close"] = float("nan")
    if change == "bad_ohlc": f.loc[1, "high"] = 5
    if change == "future": f.loc[2, "date"] = "2099-01-01"
    if change == "negative_volume": f.loc[1, "volume"] = -1
    with pytest.raises(ValueError): validate_csv(f.to_csv(index=False))


def test_dates_must_have_canonical_iso_format():
    f = bars([10, 11, 12])
    f.loc[0, 'date'] = '2020-1-1'
    with pytest.raises(ValueError, match='補零'):
        validate_csv(f.to_csv(index=False))


def test_identical_import_deduplicates_and_detects_tampering():
    a, b = imported(), imported()
    assert a["id"] == b["id"]
    filename = storage.root() / "data" / (a["fingerprint"] + ".csv")
    original = filename.read_text()
    changed = original.replace("2020-01-01", "2020-01-09", 1)
    assert changed != original  # Always corrupt a byte, independent of numeric formatting.
    filename.write_text(changed)
    with pytest.raises(ValueError, match="指紋"):
        storage.get_dataset(a["id"])


def test_end_to_end_api_queue_worker_report_and_export():
    with TestClient(app) as c:
        assert c.get('/').status_code == 200
        assert c.get('/static/app.js').status_code == 200
        payload = ImportRequest(symbol="2330", csv_text=bars(range(10, 110)).to_csv(index=False)).model_dump()
        d = c.post('/api/import', json=payload)
        assert d.status_code == 200
        dataset = d.json()
        req = JobRequest(kind="backtest", dataset_ids=[dataset["id"]]).model_dump()
        j = c.post('/api/jobs', json=req).json()
        assert j["status"] == "queued"
        run(once=True)
        result = c.get(f'/api/jobs/{j["id"]}').json()
        assert result["status"] == "done"
        assert result["result"]["succeeded"] == 1
        assert result["result"]["outputs"][str(dataset["id"])]["trades"]
        assert c.post('/api/jobs', json=req).json()["id"] == j["id"]
        assert c.get(f'/api/jobs/{j["id"]}/export').json()["result"] == result["result"]
        assert 'completed' not in c.get('/api/jobs').json()[0]['progress']
        assert c.get('/api/status').json()["llm_calls"] == 0
        short = c.get(f'/api/jobs/{j["id"]}/summary').json()
        assert short['outputs'][0]['metrics']['trades'] > 0
        assert 'curve' not in storage.canonical(short)
        assert (storage.root() / 'CONTEXT.md').is_file()


def test_invalid_rule_unknown_dataset_and_index_trade_rejected():
    with TestClient(app) as c:
        assert c.post('/api/jobs', json={"kind":"backtest","dataset_ids":[999]}).status_code == 400
        req = {"kind":"backtest","dataset_ids":[1],"strategy":{"entry":[{"field":"__import__", "op":"gt", "value":0}]}}
        assert c.post('/api/jobs', json=req).status_code == 422


def test_auth_and_cross_origin_write(monkeypatch):
    monkeypatch.setenv("STOCKLAB_API_TOKEN", "test-secret")
    with TestClient(app) as c:
        assert c.get('/api/status').status_code == 401
        headers = {"Authorization":"Bearer test-secret"}
        assert c.get('/api/status', headers=headers).status_code == 200
        assert c.put('/api/schedule', json=Schedule().model_dump(), headers={**headers,"Origin":"https://other.example"}).status_code == 403


def test_trusted_public_origin_behind_https_proxy(monkeypatch):
    monkeypatch.setenv('STOCKLAB_PUBLIC_ORIGIN', 'https://my-lab.example')
    with TestClient(app) as c:
        assert c.put('/api/schedule', json=Schedule().model_dump(), headers={'Origin':'https://my-lab.example'}).status_code == 200
        assert c.put('/api/schedule', json=Schedule().model_dump(), headers={'Origin':'https://untrusted.example'}).status_code == 403


def test_schedule_once_per_day_and_weekend_skip():
    d = imported()
    storage.setting('schedule', Schedule(enabled=True, dataset_ids=[d['id']]).model_dump())
    before = datetime(2025, 1, 6, 17, 0, tzinfo=ZoneInfo('Asia/Taipei'))
    scheduled(before)
    assert storage.jobs() == []
    after = before.replace(hour=18)
    scheduled(after)
    scheduled(after)
    assert len(storage.jobs()) == 1
    scheduled(datetime(2025, 1, 11, 19, 0, tzinfo=ZoneInfo('Asia/Taipei')))
    assert len(storage.jobs()) == 1


def test_crashed_job_requeues_and_cached_completed_items_survive():
    d = imported()
    j = storage.enqueue(JobRequest(kind='backtest', dataset_ids=[d['id']]).model_dump())
    storage.claim()
    storage.checkpoint(j['id'], {'completed': {str(d['id']): {'already':'saved'}}})
    run(once=True)
    j = storage.get_job(j['id'])
    assert j['status'] == 'done'
    assert j['result']['outputs'][str(d['id'])] == {'already':'saved'}


def test_download_mapping_and_authorization_not_in_url(monkeypatch):
    def fake_open(req, timeout):
        assert 'token=' not in req.full_url
        assert req.headers['Authorization'] == 'Bearer PRIVATE_TEST_VALUE'
        rows = [{'date':'2024-01-02','stock_id':'2330','open':10,'max':11,'min':9,'close':10,'Trading_Volume':100},
                {'date':'2024-01-03','stock_id':'2330','open':10,'max':12,'min':10,'close':11,'Trading_Volume':200}]
        return io.BytesIO(json.dumps({'status':200,'data':rows}).encode())
    monkeypatch.setenv('FINMIND_TOKEN','PRIVATE_TEST_VALUE')
    monkeypatch.setattr('urllib.request.urlopen', fake_open)
    d = download(DownloadRequest(symbol='2330',start='2024-01-02',end='2024-01-03'))
    assert d['rows'] == 2 and d['source'] == 'FinMind/TaiwanStockPrice'
    meta, frame = storage.get_dataset(d['id'])
    assert frame['high'].tolist() == [11,12]
    assert 'PRIVATE_TEST_VALUE' not in storage.canonical(meta)


def test_download_failure_does_not_substitute_demo(monkeypatch):
    def broken(*args, **kwargs): raise OSError('private request detail')
    monkeypatch.setattr('urllib.request.urlopen',broken)
    with pytest.raises(ValueError,match='連線') as e:
        finmind('TaiwanStockPrice')
    assert 'private request detail' not in str(e.value)
    assert storage.datasets() == []


def test_proxy_policy_denial_does_not_request_another_secret(monkeypatch):
    import urllib.error
    def denied(*args, **kwargs):
        raise urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden'))
    monkeypatch.setattr('urllib.request.urlopen', denied)
    with pytest.raises(ValueError, match='網域設定尚未套用'):
        finmind('TaiwanStockPrice')


def test_malformed_provider_payload_rejected(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a,**kw: io.BytesIO(b'[]'))
    with pytest.raises(ValueError, match='未成功回傳'):
        finmind('TaiwanStockPrice')
    monkeypatch.setattr('stocklab.data.finmind', lambda *a,**kw:[{'date':'2024-01-02','stock_id':'2330'}])
    with pytest.raises(ValueError, match='缺少必要行情欄位'):
        download(DownloadRequest(symbol='2330',start='2024-01-02',end='2024-01-03'))


def test_computation_cache_is_versioned_by_inputs():
    d = imported()
    calls = []
    def calculate():
        calls.append(1)
        return {"value": len(calls)}
    one = storage.cached_compute('research', d, {}, {'fee':1}, calculate)
    two = storage.cached_compute('research', d, {}, {'fee':1}, calculate)
    three = storage.cached_compute('research', d, {}, {'fee':2}, calculate)
    assert one == two == {'value':1}
    assert three == {'value':2}


def test_consistent_backup_restore_and_no_overwrite(tmp_path, monkeypatch):
    from stocklab.backup import create_backup, restore
    d = imported()
    storage.setting('schedule', Schedule(enabled=True, dataset_ids=[d['id']]).model_dump())
    archive = create_backup()
    dest = tmp_path / 'restored'
    restore(archive, dest)
    with pytest.raises(ValueError, match='不覆寫'):
        restore(archive, dest)
    monkeypatch.setenv('STOCKLAB_HOME', str(dest))
    meta, f = storage.get_dataset(d['id'])
    assert meta['fingerprint'] == d['fingerprint']
    assert len(f) == 100
    assert storage.setting('schedule')['enabled']


def test_restore_rejects_unsafe_tar(tmp_path):
    import tarfile
    from stocklab.backup import restore
    archive = tmp_path / 'unsafe.tar.gz'
    with tarfile.open(archive, 'w:gz') as t:
        for name in ('lab.sqlite3','manifest.json','../escape'):
            info = tarfile.TarInfo(name)
            info.size = 1
            t.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='不安全'):
        restore(archive, tmp_path / 'new')
    assert not (tmp_path / 'escape').exists()
