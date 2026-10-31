import numpy as np
import pytest
from fastapi.testclient import TestClient

from stocklab import ENGINE_VERSION, storage
from stocklab.app import app
from stocklab.chat import propose
from stocklab.dashboard import research_dashboard
from stocklab.engine import backtest
from stocklab.models import ChatRequest, JobRequest, Rule, Strategy
from stocklab.worker import run
from tests.conftest import bars
from tests.test_engine import free


def dataset(symbol="0050", source="TEST", size=500):
    f=bars(100+np.sin(np.arange(size)/7)*10)
    return storage.put_dataset(f,{"symbol":symbol,"name":"","asset_type":"etf","price_basis":"raw","source":source},{})


def test_chat_parses_full_conditions_costs_and_keeps_leading_zero():
    d=dataset()
    p=propose(ChatRequest(message="請幫我回測 0050 20 日均線高於 60 日均線進場，20日均線低於60日均線出場，停損5%，本金100萬，手續費0.1%"))
    assert p["ready"],p["content"]
    r=p["request"]
    assert r["dataset_ids"]==[d["id"]]
    assert r["costs"]["initial_cash"]==1000000
    assert r["costs"]["commission"]==.001
    assert r["strategy"]["stop_loss"]==.05
    assert r["strategy"]["entry"][0]=={"field":"sma20","op":"gt","value":"sma60"}
    assert p["targets"][0]["symbol"]=="0050"


@pytest.mark.parametrize("tail",["停利10%","最近三年","放空","盤中成交","跌破出場","20日均線大於200日均線進場"])
def test_chat_never_silently_ignores_unsupported_constraints(tail):
    dataset()
    p=propose(ChatRequest(message="0050，20日均線高於60日均線進場，20日均線低於60日均線出場，"+tail))
    assert not p["ready"]
    assert "request" not in p


def test_chat_crossing_and_explicit_boolean_logic():
    d=dataset()
    p=propose(ChatRequest(message="收盤價突破20日均線或RSI低於30進場，收盤價跌破20日均線出場",dataset_ids=[d["id"]]))
    assert p["ready"],p["content"]
    assert p["request"]["strategy"]["entry_mode"]=="any"
    assert p["request"]["strategy"]["entry"][0]["op"]=="cross_above"
    assert p["request"]["strategy"]["exit"][0]["op"]=="cross_below"


def test_mixed_and_or_and_missing_exit_need_clarification():
    d=dataset()
    for text in ("RSI低於30或收盤價高於20日均線且收盤價高於60日均線進場，不設出場", "RSI低於30進場"):
        assert not propose(ChatRequest(message=text,dataset_ids=[d['id']]))['ready']


def test_missing_or_ambiguous_data_does_not_use_another_instrument():
    a=dataset("0050","TEST")
    assert not propose(ChatRequest(message="2330，買入持有",dataset_ids=[a['id']]))['ready']
    dataset("0050","OTHER")
    assert not propose(ChatRequest(message="0050，買入持有"))['ready']
    assert propose(ChatRequest(message="0050，買入持有",dataset_ids=[a['id']]))['ready']


def test_research_does_not_ignore_custom_risk_constraints():
    dataset()
    assert propose(ChatRequest(message="0050，找勝率高的條件"))['request']['kind']=='research'
    assert not propose(ChatRequest(message="0050，找進場條件，停損5%"))['ready']


def test_chat_confirmation_is_required_idempotent_and_history_persists():
    d=dataset(size=100)
    with TestClient(app) as c:
        response=c.post('/api/chat',json={'message':'0050，收盤價高於0進場，不設出場','costs':free().model_dump()})
        assert response.status_code==200
        message=response.json()['assistant']
        assert message['proposal']['ready']
        assert storage.jobs()==[]
        first=c.post(f'/api/chat/{message["id"]}/run',json={}).json()
        second=c.post(f'/api/chat/{message["id"]}/run',json={}).json()
        assert first['id']==second['id']
        run(once=True)
        assert storage.get_job(first['id'])['status']=='done'
        history=c.get('/api/chat').json()
        assert len(history)==2 and history[-1]['job_id']==first['id']
        bad=c.post('/api/chat',json={'message':'自動賺錢'}).json()['assistant']
        assert c.post(f'/api/chat/{bad["id"]}/run',json={}).status_code==400


def test_followup_preserves_target_and_rules_but_revalidates_values():
    dataset()
    with TestClient(app) as c:
        first=c.post('/api/chat',json={'message':'0050，RSI低於30進場，RSI高於70出場，本金100萬'}).json()['assistant']['proposal']
        second=c.post('/api/chat',json={'message':'停損改成3%'}).json()['assistant']['proposal']
        assert second['ready']
        assert second['request']['strategy']['stop_loss']==.03
        assert second['request']['strategy']['entry']==first['request']['strategy']['entry']
        assert second['request']['dataset_ids']==first['request']['dataset_ids']
        assert second['request']['costs']['initial_cash']==1000000
        third=c.post('/api/chat',json={'message':'本金改成50萬'}).json()['assistant']['proposal']
        assert third['request']['costs']['initial_cash']==500000
        assert third['request']['strategy']['stop_loss']==.03
        cancel=c.post('/api/chat',json={'message':'取消停損'}).json()['assistant']['proposal']
        assert cancel['request']['strategy']['stop_loss'] is None
        assert not c.post('/api/chat',json={'message':'停損改成300%'}).json()['assistant']['proposal']['ready']


def test_followup_without_context_is_not_a_default_strategy():
    assert not propose(ChatRequest(message='停損改成5%'))['ready']


def test_app_shell_manifest_and_private_chat_cache_policy(monkeypatch):
    with TestClient(app) as c:
        assert c.get('/static/manifest.webmanifest').json()['display']=='standalone'
        worker=c.get('/service-worker.js')
        assert worker.status_code==200
        assert 'url.pathname.startsWith("/api/")' in worker.text
        assert c.get('/api/chat').headers['cache-control']=='no-store'
        monkeypatch.setenv('STOCKLAB_API_TOKEN','private-test')
        assert c.get('/api/chat').status_code==401
        assert c.post('/api/chat',json={'message':'測試'}).status_code==401


def test_cross_signals_execute_at_next_open_and_not_when_already_above(meta):
    f=bars([10,9,11,12,8,9])
    s=Strategy(entry=[Rule(field='close',op='cross_above',value=10)],exit=[Rule(field='close',op='cross_below',value=10)])
    r=backtest(f,meta,s,free())
    assert len(r['trades'])==1
    t=r['trades'][0]
    assert t['signal_date']==f.iloc[2]['date'] and t['entry_price']==12
    assert t['exit_signal_date']==f.iloc[4]['date'] and t['exit_price']==9
    assert backtest(bars([11]*10),meta,s,free())['metrics']['trades']==0


def metric(win=.5, trades=20, ret=.1):
    return {'win_rate':win,'trades':trades,'total_return':ret,'max_drawdown':-.1,'sharpe':1,'cagr':.1,'bars':100,'total_costs':100,'start':'2020-01-01','end':'2021-01-01'}


def add_study(d, validation_win, test_return, trades=20, nonce=''):
    req=JobRequest(kind='research',dataset_ids=[d['id']]).model_dump()
    j=storage.enqueue(req,nonce)
    strategy=req['strategy']
    output={'dataset':d,'split':{'train':['2018-01-01','2019-01-01'],'validation':['2019-01-02','2020-01-01'],'test':['2020-01-02','2021-01-01']},
            'shortlist':[{'strategy':strategy,'train':metric(.5,20),'validation':metric(validation_win,trades)}],
            'selected_strategy':strategy,'test':{'metrics':metric(.5,20,test_return),'corporate_actions':None}}
    storage.finish(j['id'],{'outputs':{str(d['id']):output},'succeeded':1,'failed':0,'errors':{}})
    return j


def test_dashboard_ranks_validation_not_heldout_and_gates_samples():
    a,b,c=dataset('A'),dataset('B'),dataset('C')
    add_study(a,.8,-.9)
    add_study(b,.6,9)
    add_study(c,1,10,trades=1)
    result=research_dashboard(min_trades=10)
    assert [x['symbol'] for x in result['candidates']]==['A','B']
    assert result['insufficient_samples']==1
    assert result['candidates'][0]['heldout_test']['total_return']==-.9


def test_dashboard_excludes_demo_old_engine_and_duplicate_evidence():
    d=dataset('A')
    add_study(d,.7,.1)
    add_study(d,.7,.1,nonce='same-data')
    demo=dataset('DEMO','SYNTHETIC_DEMO')
    add_study(demo,1,1)
    old=add_study(dataset('OLD'),1,1)
    with storage.connect() as c:
        c.execute("UPDATE jobs SET engine_version='old' WHERE id=?",(old['id'],))
    result=research_dashboard()
    assert [x['symbol'] for x in result['candidates']]==['A']
    assert result['hidden_synthetic_studies']==1
    assert len(research_dashboard(include_demo=True)['candidates'])==2
