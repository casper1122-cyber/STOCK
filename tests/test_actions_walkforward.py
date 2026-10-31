from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from stocklab import storage
from stocklab.actions import import_actions, signal_frame
from stocklab.app import app
from stocklab.engine import backtest, walkforward
from stocklab.models import ActionEvent, ActionsImport, ImportRequest, JobRequest, WalkForwardConfig
from stocklab.worker import run
from tests.conftest import bars
from tests.test_engine import always, free


def bundle(frame, kind="dividend", index=2, **values):
    event = ActionEvent(ex_date=frame.iloc[index]["date"], kind=kind, announced_date=frame.iloc[0]["date"], **values)
    return {"id":1, "source":"SYNTHETIC_TEST", "fingerprint":"test", "events":[event.model_dump(mode="json")]}


def test_dividend_does_not_create_loss_and_is_not_spendable_early(meta):
    f = bars([100, 100, 90, 90, 90])
    actions = bundle(f, cash_per_share=10, pay_date=f.iloc[4]["date"])
    r = backtest(f, meta, always(), free(), actions=actions)
    assert r["curve"][2]["equity"] == 1000
    assert r["curve"][2]["cash"] == 0
    assert r["curve"][2]["receivables"] == 100
    assert r["curve"][-1]["receivables"] == 0
    assert r["metrics"]["final_equity"] == 1000
    assert r["trades"][0]["pnl"] == 0
    assert r["trades"][0]["dividend_amount"] == 100


def test_buyer_on_ex_date_has_no_dividend_entitlement(meta):
    f = bars([100, 90, 90, 90])
    actions = bundle(f, index=1, cash_per_share=10, pay_date=f.iloc[3]["date"])
    r = backtest(f, meta, always(), free(), actions=actions)
    assert r["trades"][0]["dividend_amount"] == 0
    assert not r["corporate_action_ledger"]
    assert r["metrics"]["total_return"] == 0


def test_seller_on_ex_date_keeps_rights_and_cannot_reinvest_claim(meta):
    f = bars([100, 100, 90, 90, 90, 90])
    actions = bundle(f, cash_per_share=10, pay_date=f.iloc[5]["date"])
    strategy = always().model_copy(update={"max_hold":1})
    r = backtest(f, meta, strategy, free(), actions=actions)
    assert r["trades"][0]["exit_date"] == f.iloc[2]["date"]
    assert r["trades"][0]["dividend_amount"] == 100
    ex_day = next(x for x in r["curve"] if x["date"] == f.iloc[2]["date"])
    assert ex_day["cash"] == 900
    assert ex_day["receivables"] == 100
    assert r["trades"][1]["initial_quantity"] == 10  # 900 cash, not 1000 NAV
    assert r["metrics"]["final_equity"] == 1000


def test_unpaid_claim_valued_but_not_credited_to_cash(meta):
    f = bars([100, 100, 90, 90])
    pay = date.fromisoformat(f.iloc[-1]["date"]) + timedelta(days=30)
    r = backtest(f, meta, always(), free(), actions=bundle(f,cash_per_share=10,pay_date=pay))
    assert r["curve"][-1]["cash"] == 900
    assert r["curve"][-1]["receivables"] == 100
    assert r["pending_dividends"][0]["amount"] == 100


def test_weekend_payment_released_at_next_observation(meta):
    f = bars([100, 100, 90, 90, 90])  # Jan 3 ex, Jan 4 Saturday pay, Jan 6 observed
    r = backtest(f, meta, always(), free(), actions=bundle(f,cash_per_share=10,pay_date="2020-01-04"))
    payment = next(x for x in r["corporate_action_ledger"] if x["kind"] == "dividend_payment")
    assert payment["date"] == "2020-01-06"
    assert r["curve"][3]["receivables"] == 0


def test_split_preserves_wealth_and_original_fill_price(meta):
    f = bars([100, 100, 50, 50, 50])
    r = backtest(f, meta, always(), free(), actions=bundle(f,kind="split",split_ratio=2))
    assert r["metrics"]["total_return"] == 0
    assert r["trades"][0]["initial_quantity"] == 10
    assert r["trades"][0]["quantity"] == 20
    assert r["trades"][0]["entry_price"] == 100
    assert r["trades"][0]["exit_price"] == 50
    assert r["trades"][0]["costs"] == 0


@pytest.mark.parametrize("kind,prices,event", [
    ("dividend",[100,100,90,90,90],{"cash_per_share":10,"pay_date":"2020-01-07"}),
    ("split",[100,100,50,50,50],{"split_ratio":2}),
])
def test_corporate_action_alone_does_not_trigger_stop(meta,kind,prices,event):
    f = bars(prices)
    strategy = always().model_copy(update={"stop_loss":.05})
    r = backtest(f,meta,strategy,free(),actions=bundle(f,kind=kind,**event))
    assert len(r["trades"]) == 1
    assert r["trades"][0]["exit_reason"] == "end_of_sample_close"


def test_fractional_split_rejected_instead_of_rounded(meta):
    f = bars([100,100,600,600])
    with pytest.raises(ValueError,match="零碎股"):
        backtest(f,meta,always(),free(),actions=bundle(f,kind="split",split_ratio=.15))


def test_event_adjustment_causal_and_double_adjustment_rejected(meta):
    f = bars([100,100,50,50,50])
    events = bundle(f,kind="split",split_ratio=2)["events"]
    before = signal_frame(f,[])
    after = signal_frame(f,events)
    np.testing.assert_allclose(before.iloc[:2].select_dtypes('number'),after.iloc[:2].select_dtypes('number'))
    assert after["close"].tolist() == [100]*5
    with pytest.raises(ValueError,match="重複還原"):
        backtest(f,{**meta,"price_basis":"adjusted"},always(),free(),actions=bundle(f,kind="split",split_ratio=2))


def test_action_import_fingerprint_and_version_binding():
    f = bars([100,100,90,90,90])
    d = storage.put_dataset(f,{"symbol":"TEST","name":"", "asset_type":"stock","price_basis":"raw","source":"SYNTHETIC_TEST"},{})
    text = "ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio\n2020-01-03,dividend,2020-01-01,10,2020-01-07,\n"
    req = ActionsImport(dataset_id=d["id"],source="Synthetic action fixture",csv_text=text)
    one,two=import_actions(req),import_actions(req)
    assert one['id'] == two['id']
    with pytest.raises(ValueError,match="版本不符"):
        storage.get_actions(one['id'],999)
    with storage.connect() as c:
        c.execute("UPDATE action_bundles SET source='changed' WHERE id=?",(one['id'],))
    with pytest.raises(ValueError,match="指紋"):
        storage.get_actions(one['id'],d['id'])


@pytest.mark.parametrize("text", [
    "2020-01-03,dividend,2020-01-06,10,2020-01-07,",
    "2020-01-03,dividend,2020-01-01,10,2020-01-02,",
    "2020-01-03,split,2020-01-01,10,,2",
])
def test_invalid_or_late_event_rejected(text):
    d=storage.put_dataset(bars([100]*5),{"symbol":"T","name":"","asset_type":"stock","price_basis":"raw","source":"TEST"},{})
    with pytest.raises(ValueError):
        import_actions(ActionsImport(dataset_id=d['id'],source='test',csv_text="ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio\n"+text+"\n"))


def test_walkforward_no_winner_keeps_all_test_folds(meta):
    r=walkforward(bars([100]*700),meta,free())
    assert r['summary']['folds'] >= 2
    assert r['summary']['selected_folds'] == 0
    assert all(x['test']['metrics']['total_return']==0 for x in r['folds'])
    periods=[x['test_period'] for x in r['folds']]
    assert all(periods[i][1] < periods[i+1][0] for i in range(len(periods)-1))
    assert r['summary']['total_test_bars']==len(periods)*63


def test_walkforward_selection_ignores_current_and_future_test(meta):
    rng=np.random.default_rng(21)
    f=bars(100*np.exp(np.cumsum(rng.normal(0,.02,700))))
    first=walkforward(f,meta,free())
    altered=f.copy()
    boundary=120+252+63
    altered.loc[boundary:,["open","high","low","close"]]*=np.linspace(1,3,len(f)-boundary)[:,None]
    second=walkforward(altered,meta,free())
    assert first['folds'][0]['selected_strategy']==second['folds'][0]['selected_strategy']
    assert first['folds'][0]['shortlist']==second['folds'][0]['shortlist']
    assert first['folds'][0]['train'][1]<first['folds'][0]['validation'][0]<first['folds'][0]['test_period'][0]


def test_walkforward_does_not_silently_drop_excess_folds(meta):
    with pytest.raises(ValueError,match='超過'):
        walkforward(bars([100]*1000),meta,config=WalkForwardConfig(max_folds=2))


def test_actions_api_to_worker_and_report():
    with TestClient(app) as c:
        f=bars([100,100,90,90,90])
        d=c.post('/api/import',json=ImportRequest(symbol='FIXTURE',csv_text=f.to_csv(index=False)).model_dump()).json()
        text="ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio\n2020-01-03,dividend,2020-01-01,10,2020-01-07,\n"
        response=c.post('/api/actions',json={'dataset_id':d['id'],'source':'Synthetic test','csv_text':text})
        assert response.status_code==200
        actions=response.json()
        req=JobRequest(kind='backtest',dataset_ids=[d['id']],action_ids={d['id']:actions['id']},strategy=always(),costs=free())
        j=c.post('/api/jobs',json=req.model_dump(mode='json')).json()
        run(once=True)
        j=c.get('/api/jobs/'+str(j['id'])).json()
        assert j['status']=='done'
        output=j['result']['outputs'][str(d['id'])]
        assert output['metrics']['final_equity']==1000
        assert output['corporate_actions']['fingerprint']==actions['fingerprint']
        assert c.get('/api/actions').json()[0]['event_count']==1


def test_old_engine_checkpoint_cannot_be_reused():
    from stocklab.worker import execute
    j={'engine_version':'old'}
    with pytest.raises(ValueError,match='checkpoint'):
        execute(j)


def test_split_with_costs_preserves_original_fill_audit(meta):
    from stocklab.models import Costs
    f=bars([100,100,50,50])
    costs=Costs(initial_cash=1000,commission=.01,min_commission=0,sell_tax=.02,slippage=.01)
    r=backtest(f,meta,always(),costs,actions=bundle(f,kind='split',split_ratio=2))
    t=r['trades'][0]
    assert t['entry_price']==101
    assert t['initial_quantity']==9 and t['quantity']==18
    assert t['exit_price']==49.5
    assert t['costs']==pytest.approx(53.82)
    assert t['pnl']==pytest.approx(-53.82)
    assert r['metrics']['final_equity']==pytest.approx(946.18)


def test_storage_preserves_price_precision_and_large_integer_volume():
    f=bars([1.23456789012345,1.23456789012346,1.23456789012347])
    f['volume']=1234567890123
    meta={'symbol':'PRECISION','name':'','asset_type':'stock','price_basis':'raw','source':'TEST'}
    d=storage.put_dataset(f,meta,{})
    _,loaded=storage.get_dataset(d['id'])
    np.testing.assert_array_equal(loaded['close'].to_numpy(),f['close'].to_numpy())
    assert loaded['volume'].tolist()==[1234567890123]*3


def test_walkforward_api_summary_and_configuration_cache():
    with TestClient(app) as c:
        f=bars([100]*700)
        d=c.post('/api/import',json=ImportRequest(symbol='ROLLING_DEMO',csv_text=f.to_csv(index=False)).model_dump()).json()
        req=JobRequest(kind='walkforward',dataset_ids=[d['id']],costs=free())
        j=c.post('/api/jobs',json=req.model_dump(mode='json')).json()
        run(once=True)
        short=c.get('/api/jobs/'+str(j['id'])+'/summary').json()
        assert short['status']=='done'
        assert short['outputs'][0]['scope']=='rolling_out_of_sample'
        assert short['outputs'][0]['walkforward_summary']['selected_folds']==0
        assert short['outputs'][0]['metrics'] is None
        calls=[]
        def calculate():
            calls.append(1)
            return {'call':len(calls)}
        storage.cached_compute('walkforward',d,{}, {},calculate,parameters={'test_bars':63})
        storage.cached_compute('walkforward',d,{}, {},calculate,parameters={'test_bars':63})
        storage.cached_compute('walkforward',d,{}, {},calculate,parameters={'test_bars':126})
        assert len(calls)==2
