import pytest
from fastapi.testclient import TestClient

from stocklab.app import app
from stocklab.production import settings


def environment(tmp_path):
    return {"STOCKLAB_API_TOKEN":"x"*40,"RENDER_EXTERNAL_URL":"https://research.example","PORT":"10000","STOCKLAB_HOME":str(tmp_path)}


def test_host_configuration_uses_https_origin_and_persistent_path(tmp_path):
    s=settings(environment(tmp_path))
    assert s.port==10000 and s.origin=='https://research.example'
    assert s.data_home==tmp_path and not s.demo_mode
    assert 'x'*40 not in repr(s)


@pytest.mark.parametrize('change',[{'STOCKLAB_API_TOKEN':''},{'RENDER_EXTERNAL_URL':'http://public.example'},{'PORT':'bad'},{'PORT':'99999'},{'STOCKLAB_NO_WORKER':'1'},{'RENDER_EXTERNAL_URL':'https://example/a'},{'RENDER_EXTERNAL_URL':'https://user:pass@example'}])
def test_host_refuses_unsafe_or_invalid_settings(tmp_path,change):
    env=environment(tmp_path);env.update(change)
    with pytest.raises(ValueError):settings(env)


def test_persistent_mode_requires_explicit_data_path(tmp_path):
    env=environment(tmp_path);env.pop('STOCKLAB_HOME')
    with pytest.raises(ValueError,match='持久磁碟'):settings(env)
    env['STOCKLAB_DEMO_MODE']='1'
    assert settings(env).demo_mode


def test_public_health_has_no_private_data_and_api_needs_key(monkeypatch):
    monkeypatch.setenv('STOCKLAB_API_TOKEN','x'*40)
    with TestClient(app) as c:
        health=c.get('/healthz').json()
        assert health['status']=='ok' and 'datasets' not in health
        assert c.get('/app-info').json()['requires_key']
        assert c.get('/api/chat').status_code==401
        assert c.get('/api/chat',headers={'Authorization':'Bearer '+'x'*40}).status_code==200


def test_preview_does_not_accept_private_histories_or_schedules(monkeypatch):
    monkeypatch.setenv('STOCKLAB_DEMO_MODE','1')
    with TestClient(app) as c:
        assert c.get('/app-info').json()['demo_mode']
        for path in ('import','download','actions','calendars','schedule'):
            assert c.post('/api/'+path,json={}).status_code==409
