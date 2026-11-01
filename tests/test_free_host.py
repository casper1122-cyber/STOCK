"""Free hosting preserves the research engine; only storage/availability change."""
import tarfile
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from stocklab import storage
from stocklab.app import app
from stocklab.backup import restore
from stocklab.models import ImportRequest, JobRequest, Schedule
from stocklab.production import settings
from stocklab.worker import run, scheduled
from tests.conftest import bars


def test_free_host_still_requires_strong_access_control():
    import pytest
    env={"STOCKLAB_EPHEMERAL_MODE":"1","RENDER_EXTERNAL_URL":"https://lab.example"}
    with pytest.raises(ValueError,match="32"):
        settings(env)
    env["STOCKLAB_API_TOKEN"]="x"*40
    config=settings(env)
    assert config.ephemeral_mode and not config.demo_mode
    assert config.data_home.name=="stocklab-free"


def test_free_host_import_backtest_and_authenticated_portable_backup(tmp_path,monkeypatch):
    monkeypatch.setenv("STOCKLAB_EPHEMERAL_MODE","1")
    token="test-only-"+"x"*40
    monkeypatch.setenv("STOCKLAB_API_TOKEN",token)
    monkeypatch.setattr(tempfile,"tempdir",str(tmp_path))
    # Unrelated private files must never be included in the exported archive.
    (storage.root()/".env").write_text("private="+token)
    headers={"Authorization":"Bearer "+token}
    with TestClient(app) as client:
        assert client.get("/api/backup").status_code==401
        assert not list(tmp_path.glob("stocklab-export-*"))
        info=client.get("/app-info").json()
        assert info["ephemeral_mode"] and not info["demo_mode"]
        assert client.get("/api/datasets",headers=headers).json()==[]
        request=ImportRequest(symbol="FREE_TEST",name="合成軟體測試，非市場資料",csv_text=bars(range(10,110)).to_csv(index=False))
        response=client.post("/api/import",json=request.model_dump(),headers=headers)
        assert response.status_code==200
        dataset=response.json()
        job=client.post("/api/jobs",json=JobRequest(kind="backtest",dataset_ids=[dataset["id"]]).model_dump(),headers=headers).json()
        run(once=True)
        completed=client.get(f'/api/jobs/{job["id"]}',headers=headers).json()
        assert completed["status"]=="done" and completed["result"]["succeeded"]==1
        response=client.get("/api/backup",headers=headers)
        assert response.status_code==200 and response.headers["cache-control"]=="no-store"
        assert "attachment" in response.headers["content-disposition"]
        assert not list(tmp_path.glob("stocklab-export-*"))
    archive=tmp_path/"saved.tar.gz"
    archive.write_bytes(response.content)
    with tarfile.open(archive,"r:gz") as tar:
        names=tar.getnames()
        assert set(names)=={"lab.sqlite3","manifest.json","data/"+dataset["fingerprint"]+".csv"}
        assert token.encode() not in tar.extractfile("lab.sqlite3").read()
    destination=tmp_path/"restored"
    restore(archive,destination)
    monkeypatch.setenv("STOCKLAB_HOME",str(destination))
    meta,frame=storage.get_dataset(dataset["id"])
    assert meta["fingerprint"]==dataset["fingerprint"] and len(frame)==100
    assert storage.get_job(job["id"])["result"]==completed["result"]


def test_free_host_disables_stored_schedules_without_losing_configuration(monkeypatch):
    monkeypatch.setenv("STOCKLAB_EPHEMERAL_MODE","1")
    saved=Schedule(enabled=True,dataset_ids=[1]).model_dump()
    storage.setting("schedule",saved)
    scheduled(datetime(2026,11,2,19,tzinfo=ZoneInfo("Asia/Taipei")))
    assert storage.jobs()==[]
    with TestClient(app) as client:
        for method in (client.put,client.post):
            assert method("/api/schedule",json=saved).status_code==409
        current=client.get("/api/schedule").json()
        assert not current["enabled"] and not current["available"]
    assert storage.setting("schedule")==saved
