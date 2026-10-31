import json
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stocklab import storage
from stocklab.app import app
from stocklab.audit import get_audit, get_calendar, import_calendar, run_audit, validate_job_audit
from stocklab.chat import propose
from stocklab.engine import backtest
from stocklab.models import AuditRequest, CalendarImport, ChatRequest, JobRequest, WalkForwardConfig
from stocklab.risk import equity_statistics, trade_statistics, wilson_interval
from stocklab.worker import run
from tests.conftest import bars
from tests.test_engine import always,free


def fixture(prices=None):
    f=bars(prices or [100,100,90,95,95])
    meta={'symbol':'AUDIT_DEMO','name':'','asset_type':'stock','price_basis':'raw','source':'SYNTHETIC_DEMO'}
    return storage.put_dataset(f,meta,{'volume_missing':False}),f


def calendar(frame, closed=None, start=None, end=None):
    dates=pd.date_range(start or frame.iloc[0]['date'],end or frame.iloc[-1]['date'],freq='D')
    values=pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'is_open':[(int(d.weekday()<5) if str(d.date())!=closed else 0) for d in dates]})
    return import_calendar(CalendarImport(market='TWSE',source='SYNTHETIC calendar, not official',csv_text=values.to_csv(index=False)))


def test_calendar_matches_explicit_dates_without_claiming_source_verified():
    d,f=fixture()
    c=calendar(f)
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    assert r['status']=='passed_selected_checks'
    assert r['open_day_coverage']==1
    assert not r['source_independently_verified']
    assert r['missing_open_dates']==[]
    again=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    assert r['id']==again['id']


def test_missing_open_and_closed_day_prices_are_separate():
    d,f=fixture()
    # Price on the declared closed first day, and omit a distinct declared open day.
    broken=f.drop(index=2).reset_index(drop=True)
    b=storage.put_dataset(broken,{k:d[k] for k in ('symbol','name','asset_type','price_basis','source')},{})
    c=calendar(f,closed=f.iloc[0]['date'])
    r=run_audit(AuditRequest(dataset_id=b['id'],calendar_id=c['id']))
    assert r['status']=='issues_found'
    assert r['missing_open_dates']==[f.iloc[2]['date']]
    assert r['closed_day_prices']==[f.iloc[0]['date']]


def test_calendar_cannot_assume_weekends_or_unknown_dates():
    d,f=fixture()
    text='date,is_open\n2020-01-03,1\n2020-01-06,1\n'
    with pytest.raises(ValueError,match='逐日完整'):
        import_calendar(CalendarImport(market='TWSE',source='test',csv_text=text))
    c=calendar(f,start='2020-01-02',end='2020-01-07')
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    assert r['uncovered_calendar_dates']==['2020-01-01']
    assert r['status']=='issues_found'


def test_no_calendar_is_unchecked_not_a_pass():
    d,_=fixture()
    r=run_audit(AuditRequest(dataset_id=d['id']))
    assert r['status']=='needs_calendar'
    assert r['open_day_coverage'] is None
    req=JobRequest(kind='backtest',dataset_ids=[d['id']],audit_ids={d['id']:r['id']},require_audit=True)
    with pytest.raises(ValueError,match='尚未通過'):
        validate_job_audit(req,d['id'],d,None)


def test_calendar_and_audit_fingerprints_detect_tampering():
    d,f=fixture()
    c=calendar(f)
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    with storage.connect() as db:
        db.execute("UPDATE calendars SET source='changed' WHERE id=?",(c['id'],))
    with pytest.raises(ValueError,match='指紋'):
        get_calendar(c['id'])
    with storage.connect() as db:
        db.execute("UPDATE data_audits SET report='{}' WHERE id=?",(r['id'],))
    with pytest.raises(ValueError,match='指紋'):
        get_audit(r['id'])


def test_audit_is_bound_to_exact_prices_and_action_version():
    from stocklab.actions import import_actions
    from stocklab.models import ActionsImport
    d,f=fixture([100,100,50,50,50])
    c=calendar(f)
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    a=import_actions(ActionsImport(dataset_id=d['id'],source='synthetic',csv_text='ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio\n2020-01-03,split,2020-01-01,,,2\n'))
    req=JobRequest(kind='backtest',dataset_ids=[d['id']],action_ids={d['id']:a['id']},audit_ids={d['id']:r['id']})
    with pytest.raises(ValueError,match='版本不一致'):
        validate_job_audit(req,d['id'],d,a)
    checked=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id'],action_id=a['id']))
    assert checked['raw_moves_over_20pct']==['2020-01-03']
    assert checked['unexplained_moves_over_20pct']==[]


def test_quality_gate_api_to_worker_attaches_audit_snapshot():
    d,f=fixture()
    c=calendar(f)
    with TestClient(app) as api:
        report=api.post('/api/audits',json={'dataset_id':d['id'],'calendar_id':c['id']}).json()
        req=JobRequest(kind='backtest',dataset_ids=[d['id']],strategy=always(),costs=free(),require_audit=True)
        assert api.post('/api/jobs',json=req.model_dump(mode='json')).status_code==400
        req.audit_ids={d['id']:report['id']}
        j=api.post('/api/jobs',json=req.model_dump(mode='json')).json()
        run(once=True)
        output=api.get('/api/jobs/'+str(j['id'])).json()['result']['outputs'][str(d['id'])]
        assert output['data_audit']['fingerprint']==report['fingerprint']
        assert api.get('/api/audits/'+str(report['id'])+'/export').json()['id']==report['id']


def test_strict_chat_checks_audit_and_captures_walkforward_settings():
    d,f=fixture([100]*900)
    no=propose(ChatRequest(message='買入持有',dataset_ids=[d['id']],require_audit=True))
    assert not no['ready']
    c=calendar(f)
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    p=propose(ChatRequest(message='滾動驗證',dataset_ids=[d['id']],require_audit=True,audit_ids={d['id']:r['id']},walk_forward=WalkForwardConfig(train_bars=126,validation_bars=30,test_bars=30)))
    assert p['ready'],p['content']
    assert p['request']['walk_forward']['train_bars']==126
    assert any('126' in x and '30' in x for x in p['description'])


def test_chat_reports_insufficient_indicator_history_before_queueing():
    d,_=fixture([100]*50)
    p=propose(ChatRequest(message='120日均線高於20日均線進場，不設出場',dataset_ids=[d['id']]))
    assert not p['ready'] and '122' in p['content']
    assert storage.jobs()==[]


def test_duplicate_price_headers_are_not_silently_renamed():
    from stocklab.data import validate_csv
    text='date,open,high,low,close,close\n2020-01-01,1,1,1,1,99\n2020-01-02,1,1,1,1,99\n'
    with pytest.raises(ValueError,match='標題重複'):
        validate_csv(text)


def test_wilson_small_sample_is_not_certain_and_no_trades_has_no_interval():
    assert wilson_interval(0,0) is None
    one=wilson_interval(1,1)
    assert one['lower']==pytest.approx(.2065493144)
    assert one['upper']==pytest.approx(1)
    many=wilson_interval(100,100)
    assert many['lower']>one['lower']
    with pytest.raises(ValueError):wilson_interval(3,2)


def test_risk_metrics_explain_profit_factor_and_expectancy():
    pnl=[100,-50,-50,0,200,-100]
    stats=trade_statistics([{'pnl':p,'entry_cost':1000} for p in pnl])
    assert stats['profit_factor']==1.5
    assert stats['expectancy_cash']==pytest.approx(100/6)
    assert stats['expectancy_return']==pytest.approx(.1/6)
    assert stats['average_win']==150 and stats['average_loss']==pytest.approx(-200/3)
    assert stats['max_consecutive_losses']==2
    assert stats['winning_trades']==2 and stats['losing_trades']==3 and stats['breakeven_trades']==1


def test_no_losses_does_not_export_infinity_or_false_profit_factor():
    stats=trade_statistics([{'pnl':100,'entry_cost':1000}])
    assert stats['profit_factor'] is None and stats['profit_factor_status']=='no_losses_observed'
    assert trade_statistics([])['expectancy_cash'] is None
    storage.canonical(stats)


def test_drawdown_duration_includes_unrecovered_tail():
    equity=np.array([100,90,95,100,99,98,97,96],dtype=float)
    returns=np.diff(equity)/equity[:-1]
    stats=equity_statistics(equity,returns,-.1)
    assert stats['longest_drawdown_bars']==4
    assert stats['current_drawdown_bars']==4
    assert stats['drawdown_unrecovered']
    assert math.isfinite(stats['annual_volatility'])


def test_risk_is_part_of_real_engine_output():
    d,f=fixture()
    r=backtest(f,d,always(),free())
    m=r['metrics']
    assert m['winning_trades']+m['losing_trades']+m['breakeven_trades']==m['trades']
    assert m['win_rate_interval95']['lower']>=0
    assert 'longest_drawdown_bars' in m


def test_price_metadata_mismatch_is_not_treated_as_complete_data():
    d,_=fixture()
    with storage.connect() as db:
        db.execute("UPDATE datasets SET rows=999 WHERE id=?",(d['id'],))
    with pytest.raises(ValueError,match='中繼資料'):
        run_audit(AuditRequest(dataset_id=d['id']))


def test_calendar_date_metadata_mismatch_is_detected():
    _,f=fixture()
    c=calendar(f)
    with storage.connect() as db:
        db.execute("UPDATE calendars SET first_date='1990-01-01' WHERE id=?",(c['id'],))
    with pytest.raises(ValueError,match='中繼資料'):
        get_calendar(c['id'])


def test_known_failed_audit_study_is_not_promoted_in_ranking():
    from tests.test_chat_dashboard import add_study
    from stocklab.dashboard import research_dashboard
    d,_=fixture()
    j=add_study(d,1,1)
    job=storage.get_job(j['id'])
    output=job['result']['outputs'][str(d['id'])]
    output['data_audit']={'status':'issues_found'}
    storage.finish(j['id'],job['result'])
    result=research_dashboard(include_demo=True)
    assert result['candidates']==[]
    assert result['studies_with_failed_audits']==1


def test_legacy_audit_schema_migrates_without_rewriting_evidence():
    d,f=fixture()
    c=calendar(f)
    r=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    with storage.connect() as db:
        db.executescript('''CREATE TABLE legacy_audits (id INTEGER PRIMARY KEY,dataset_id INTEGER NOT NULL,fingerprint TEXT UNIQUE NOT NULL,report TEXT NOT NULL,created TEXT NOT NULL);
            INSERT INTO legacy_audits SELECT id,dataset_id,fingerprint,report,created FROM data_audits;
            DROP TABLE data_audits;
            ALTER TABLE legacy_audits RENAME TO data_audits;''')
    storage.init()
    restored=get_audit(r['id'])
    assert restored['fingerprint']==r['fingerprint']
    assert restored['action_id'] is None
    fresh=run_audit(AuditRequest(dataset_id=d['id']))
    assert fresh['id']>r['id']


def test_database_context_closes_connections_and_rolls_back():
    import sqlite3
    with storage.connect() as db:
        db.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')
    with pytest.raises(ValueError):
        with storage.connect() as db:
            db.execute("INSERT INTO settings VALUES ('rollback-test','true')")
            raise ValueError('interrupt')
    assert storage.setting('rollback-test') is None


def test_latest_audit_lookup_does_not_drop_markets_after_200_entries():
    from stocklab.audit import audits
    f=bars([100]*3)
    c=calendar(f)
    for i in range(205):
        d=storage.put_dataset(f,{'symbol':f'T{i}','name':'','asset_type':'stock','price_basis':'raw','source':'TEST'}, {})
        run_audit(AuditRequest(dataset_id=d['id'],calendar_id=c['id']))
    assert len(audits())==205


def test_rechecking_old_failure_cannot_silently_fall_back_to_newer_pass():
    from stocklab.audit import audits
    d,f=fixture()
    bad=calendar(f,closed=f.iloc[0]['date'])
    failed=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=bad['id']))
    good=calendar(f)
    passed=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=good['id']))
    assert audits()[0]['id']==passed['id']
    repeated=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=bad['id']))
    assert repeated['id']==failed['id']
    assert audits()[0]['id']==failed['id']
    assert audits()[0]['status']=='issues_found'


def test_current_data_failure_quarantines_a_historically_good_study():
    from tests.test_chat_dashboard import add_study
    from stocklab.dashboard import research_dashboard
    d,f=fixture()
    add_study(d,1,1)
    bad=calendar(f,closed=f.iloc[0]['date'])
    run_audit(AuditRequest(dataset_id=d['id'],calendar_id=bad['id']))
    result=research_dashboard(include_demo=True)
    assert not result['candidates']
    assert result['studies_with_failed_audits']==1


def test_strict_mode_cannot_bypass_active_failure_with_an_old_pass():
    d,f=fixture()
    good=calendar(f)
    passed=run_audit(AuditRequest(dataset_id=d['id'],calendar_id=good['id']))
    bad=calendar(f,closed=f.iloc[0]['date'])
    run_audit(AuditRequest(dataset_id=d['id'],calendar_id=bad['id']))
    req=JobRequest(kind='backtest',dataset_ids=[d['id']],audit_ids={d['id']:passed['id']},require_audit=True)
    with pytest.raises(ValueError,match='目前資料檢查發現問題'):
        validate_job_audit(req,d['id'],d,None)
    run_audit(AuditRequest(dataset_id=d['id'],calendar_id=good['id']))
    assert validate_job_audit(req,d['id'],d,None)['id']==passed['id']
