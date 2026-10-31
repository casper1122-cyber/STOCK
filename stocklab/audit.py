"""Versioned, explicitly scoped data checks against a declared calendar."""
import csv
import hashlib
import io
import json

import pandas as pd

from stocklab import storage
from stocklab.actions import signal_frame, validate_events

AUDIT_VERSION = "1"


def digest(value):
    return hashlib.sha256(storage.canonical(value).encode()).hexdigest()


def import_calendar(request):
    source=request.source.strip()
    if not source:
        raise ValueError("請提供有意義的日曆來源與版本")
    try:
        reader=csv.reader(io.StringIO(request.csv_text.lstrip("\ufeff")))
        header=next(reader)
        if len(header)!=len(set(header)):
            raise ValueError("日曆欄位標題重複")
        f=pd.read_csv(io.StringIO(request.csv_text.lstrip("\ufeff")),dtype=str,keep_default_na=False)
    except (StopIteration,csv.Error,pd.errors.ParserError,pd.errors.EmptyDataError):
        raise ValueError("日曆 CSV 無法解析") from None
    if not {"date","is_open"}.issubset(f.columns) or set(f.columns)-{"date","is_open","reason"}:
        raise ValueError("日曆需 date,is_open，可選 reason；請勿混入未知欄位")
    if not 2<=len(f)<=50000 or not f["date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError("日曆需 2 至 50000 筆 YYYY-MM-DD 日期")
    dates=pd.to_datetime(f["date"],format="%Y-%m-%d",errors="coerce")
    if dates.isna().any() or f["date"].duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("日曆日期無效、重複或未遞增")
    if not f["is_open"].isin(["0","1"]).all():
        raise ValueError("is_open 僅接受 0（休市）或 1（開市）")
    if (dates.iloc[-1]-dates.iloc[0]).days+1!=len(f):
        raise ValueError("日曆需逐日完整列出，包括休市日；不可用只列平日的清單猜測假日")
    f["is_open"]=f["is_open"].astype(int)
    if "reason" not in f:
        f["reason"]=""
    if (f["reason"].str.len()>200).any():
        raise ValueError("每筆日曆說明最多 200 字")
    records=f[["date","is_open","reason"]].to_dict(orient="records")
    body={"market":request.market,"source":source,"records":records}
    fingerprint=digest(body)
    with storage.connect() as c:
        c.execute("INSERT OR IGNORE INTO calendars(fingerprint,market,source,records,first_date,last_date,created) VALUES (?,?,?,?,?,?,?)",
                  (fingerprint,request.market,source,storage.canonical(records),records[0]["date"],records[-1]["date"],storage.now()))
        identifier=c.execute("SELECT id FROM calendars WHERE fingerprint=?",(fingerprint,)).fetchone()[0]
    return {k:v for k,v in get_calendar(identifier).items() if k!="records"}


def calendars():
    with storage.connect() as c:
        return [dict(r) for r in c.execute("SELECT id,fingerprint,market,source,first_date,last_date,created FROM calendars ORDER BY id DESC")]


def get_calendar(identifier):
    with storage.connect() as c:
        row=c.execute("SELECT * FROM calendars WHERE id=?",(identifier,)).fetchone()
    if not row:
        raise ValueError("找不到日曆版本")
    value=dict(row)
    value["records"]=json.loads(value["records"])
    if digest({k:value[k] for k in ("market","source","records")})!=value["fingerprint"]:
        raise ValueError("日曆指紋不符，停止檢查")
    if value["first_date"]!=value["records"][0]["date"] or value["last_date"]!=value["records"][-1]["date"]:
        raise ValueError("日曆範圍中繼資料不一致")
    return value


def run_audit(request):
    meta,frame=storage.get_dataset(request.dataset_id)
    actions=storage.get_actions(request.action_id,meta["id"]) if request.action_id else None
    events=validate_events(actions["events"],frame,meta) if actions else []
    calendar=get_calendar(request.calendar_id) if request.calendar_id else None
    dates=set(frame["date"])
    first,last=meta["first_date"],meta["last_date"]
    missing,closed,uncovered=[],[],[]
    expected=set()
    blocking=[]
    if calendar:
        bydate={r["date"]:r["is_open"] for r in calendar["records"]}
        coverage=pd.date_range(first,last,freq="D").strftime("%Y-%m-%d")
        uncovered=[d for d in coverage if d not in bydate]
        expected={d for d in coverage if bydate.get(d)==1}
        missing=sorted(expected-dates)
        closed=sorted(d for d in dates if bydate.get(d)==0)
        if uncovered:blocking.append("日曆未覆蓋完整價格區間")
        if missing:blocking.append("開市日缺少價格；可能是缺資料或停牌，需取得例外證據後處理")
        if closed:blocking.append("休市日出現價格；需核對交易日定義或資料來源")
    adjusted=signal_frame(frame,events) if events else frame
    raw_move=frame["close"].pct_change(fill_method=None)
    remaining=adjusted["close"].pct_change(fill_method=None)
    suspicious=frame.loc[remaining.abs()>.2,"date"].tolist()
    zero=frame.loc[frame["volume"]==0,"date"].tolist()
    review=["日曆來源、商品市場身分與公司行動完整性未獨立驗證；不代表全市場資料完整。",
            "未檢查盤中停牌、漲跌停可成交性、報價修訂時點、歷史成分股或下市母體。",
            "幣別與成交量單位未獨立稽核；現股帳務仍採台幣模型，不能據此認證海外商品。"]
    if meta["price_basis"]=="raw" and actions is None:review.append("未還原價格且未提供公司行動，不能當作含息績效。")
    if suspicious:review.append("事件調整後仍有超過20%的單日跳動；這是人工複核旗標，不等同已確認錯價。")
    if zero:review.append("存在零成交量日，回測成交可行性需要另外核對。")
    if meta["source"]=="SYNTHETIC_DEMO":review.insert(0,"合成示範，只驗證軟體流程。")
    report={"audit_version":AUDIT_VERSION,"dataset_id":meta["id"],"dataset_fingerprint":meta["fingerprint"],
            "action_id":actions["id"] if actions else None,"actions_fingerprint":actions["fingerprint"] if actions else None,
            "calendar_id":calendar["id"] if calendar else None,"calendar_fingerprint":calendar["fingerprint"] if calendar else None,
            "calendar_source":calendar["source"] if calendar else None,"declared_market":calendar["market"] if calendar else None,
            "status":"needs_calendar" if calendar is None else "issues_found" if blocking else "passed_selected_checks",
            "source_independently_verified":False,"first_date":first,"last_date":last,"price_rows":len(frame),
            "expected_open_days":len(expected) if calendar else None,
            "open_day_coverage":len(expected & dates)/len(expected) if expected else None,
            "missing_open_dates":missing,"closed_day_prices":closed,"uncovered_calendar_dates":uncovered,
            "raw_moves_over_20pct":frame.loc[raw_move.abs()>.2,"date"].tolist(),
            "unexplained_moves_over_20pct":suspicious,"zero_volume_dates":zero,
            "provided_events":len(events),"blocking_issues":blocking,"review_items":review,
            "scope":"價格指紋、明示日曆日期比對、事件口徑與跳價／零量旗標；不是完整資料認證。"}
    fingerprint=digest(report)
    with storage.connect() as c:
        c.execute("INSERT OR IGNORE INTO data_audits(dataset_id,fingerprint,report,created,action_id) VALUES (?,?,?,?,?)",
                  (meta["id"],fingerprint,storage.canonical(report),storage.now(),actions["id"] if actions else None))
        identifier=c.execute("SELECT id FROM data_audits WHERE fingerprint=?",(fingerprint,)).fetchone()[0]
        c.execute("INSERT OR REPLACE INTO audit_selections(dataset_id,action_key,audit_id,selected_at) VALUES (?,?,?,?)",
                  (meta["id"],actions["id"] if actions else 0,identifier,storage.now()))
    return get_audit(identifier)


def get_audit(identifier):
    with storage.connect() as c:
        row=c.execute("SELECT * FROM data_audits WHERE id=?",(identifier,)).fetchone()
    if not row:
        raise ValueError("找不到資料檢查報告")
    report=json.loads(row["report"])
    if digest(report)!=row["fingerprint"]:
        raise ValueError("資料檢查報告指紋不符")
    if report["dataset_id"]!=row["dataset_id"] or report["action_id"]!=row["action_id"]:
        raise ValueError("檢查報告的價格版本中繼資料不一致")
    return {**report,"id":row["id"],"fingerprint":row["fingerprint"],"created":row["created"]}


def audits():
    with storage.connect() as c:
        rows=c.execute("""WITH latest_prices AS (
          SELECT MAX(id) id FROM datasets GROUP BY symbol,asset_type,price_basis,source
        ), latest_reports AS (
          SELECT MAX(a.id) id FROM data_audits a JOIN latest_prices p ON p.id=a.dataset_id
          GROUP BY a.dataset_id,a.action_id
        ), preferred_reports AS (
          SELECT COALESCE(s.audit_id,r.id) id,s.selected_at FROM latest_reports r
          JOIN data_audits a ON a.id=r.id LEFT JOIN audit_selections s
          ON s.dataset_id=a.dataset_id AND s.action_key=COALESCE(a.action_id,0)
        ) SELECT a.id,a.dataset_id,a.fingerprint,a.created,a.action_id,r.selected_at,json_extract(a.report,'$.status') status
          FROM data_audits a JOIN preferred_reports r ON r.id=a.id ORDER BY a.id DESC""").fetchall()
    return [dict(r) for r in rows]


def validate_job_audit(request,identifier,meta,actions):
    audit_id=request.audit_ids.get(identifier)
    if not audit_id:
        if request.require_audit:
            raise ValueError("已啟用資料檢查要求，但此價格版本尚未選定檢查報告")
        return None
    report=get_audit(audit_id)
    if report["audit_version"]!=AUDIT_VERSION or report["dataset_fingerprint"]!=meta["fingerprint"] or report["actions_fingerprint"]!=(actions["fingerprint"] if actions else None):
        raise ValueError("資料檢查報告與目前價格／公司行動版本不一致，請重新檢查")
    if report["calendar_id"]:
        calendar=get_calendar(report["calendar_id"])
        if calendar["fingerprint"]!=report["calendar_fingerprint"]:
            raise ValueError("檢查使用的日曆版本不一致")
    if request.require_audit:
        action_id=actions["id"] if actions else None
        with storage.connect() as c:
            current=c.execute("""SELECT COALESCE(
              (SELECT audit_id FROM audit_selections WHERE dataset_id=? AND action_key=?),
              (SELECT id FROM data_audits WHERE dataset_id=? AND action_id IS ? ORDER BY id DESC LIMIT 1)
            ) current_id""",(identifier,action_id or 0,identifier,action_id)).fetchone()["current_id"]
        if current and get_audit(current)["status"]=="issues_found":
            raise ValueError("目前資料檢查發現問題，請先核對並重新檢查；指定日期與版本檢查尚未通過。")
    if request.require_audit and report["status"]!="passed_selected_checks":
        raise ValueError("指定資料檢查尚未通過，不能在要求通過的模式執行")
    return report
