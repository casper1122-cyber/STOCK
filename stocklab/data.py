import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stocklab import storage


def validate_csv(text):
    try:
        header = next(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    except (StopIteration,csv.Error):
        raise ValueError("CSV 缺少有效欄位標題") from None
    if len(header) != len(set(header)):
        raise ValueError("CSV 欄位標題重複，請先核對來源，不能默默選取其中一欄")
    try:
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype={"date": str}, float_precision="round_trip")
    except (ValueError, pd.errors.ParserError) as e:
        raise ValueError("CSV 無法解析") from e
    required = ["date", "open", "high", "low", "close"]
    if not set(required).issubset(frame.columns):
        raise ValueError("CSV 必須有 date,open,high,low,close；volume 可選")
    if len(frame) < 2 or len(frame) > 50000:
        raise ValueError("每個商品需有 2 至 50000 筆日線")
    if not frame["date"].astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError("日期格式必須為 YYYY-MM-DD，月份及日期須補零")
    dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or frame["date"].duplicated().any():
        raise ValueError("日期格式必須為 YYYY-MM-DD，且不得重複")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    if (dates.dt.date > today).any():
        raise ValueError("資料包含未來日期")
    if not dates.is_monotonic_increasing:
        raise ValueError("CSV 日期必須由舊至新排列；請先確認排序")
    volume_missing = "volume" not in frame
    if volume_missing:
        frame["volume"] = 0.0
    columns = required + ["volume"]
    frame = frame[columns].copy()
    for col in columns[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    numbers = frame[columns[1:]].to_numpy(dtype=float)
    if not np.isfinite(numbers).all() or (frame[required[1:]] <= 0).any().any() or (frame["volume"] < 0).any():
        raise ValueError("OHLC 必須為有限正數，成交量必須非負；不可有空值")
    if ((frame["high"] < frame[["open", "low", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "high", "close"]].min(axis=1))).any():
        raise ValueError("OHLC 不一致：high 須為最高價、low 須為最低價")
    gaps = dates.diff().dt.days
    returns = frame["close"].pct_change(fill_method=None)
    quality = {
        "volume_missing": volume_missing,
        "zero_volume_rows": int((frame["volume"] == 0).sum()),
        "gaps_over_7_calendar_days": [str(x) for x in frame.loc[gaps > 7, "date"].head(20)],
        "moves_over_20pct": [str(x) for x in frame.loc[returns.abs() > .2, "date"].head(20)],
        "calendar_verified": False,
        "corporate_actions_verified": False,
        "notes": "長假、停牌、公司行動可能造成缺口或跳價，需人工稽核；尚未比對官方交易日曆。",
    }
    return frame, quality


def import_csv(request):
    frame, quality = validate_csv(request.csv_text)
    meta = request.model_dump(exclude={"csv_text"})
    meta["source"] = "CSV (使用者提供，來源未獨立稽核)"
    return storage.put_dataset(frame, meta, quality)


def finmind(dataset, **params):
    params["dataset"] = dataset
    token = os.environ.get("FINMIND_TOKEN")
    url = "https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "TaiwanStockLab/0.1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as e:
        raise ValueError(f"FinMind HTTP {e.code}；請檢查網路允許清單、服務配額與授權。未自動重試。") from None
    except urllib.error.URLError as e:
        if "Tunnel connection failed: 403" in str(e.reason):
            raise ValueError("雲端代理拒絕連線 api.finmindtrade.com；網域設定尚未套用至執行環境，這不代表缺少 API 金鑰。") from None
        raise ValueError("FinMind 連線失敗；請檢查環境網路與服務狀態。") from None
    except (OSError, ValueError):
        raise ValueError("FinMind 連線或回應解析失敗；請檢查環境網路與服務狀態。") from None
    if not isinstance(result, dict) or result.get("status") != 200 or not isinstance(result.get("data"), list):
        raise ValueError("FinMind 未成功回傳資料；請檢查商品代號、配額或資料方案。")
    return result["data"]


def download(request, previous=None):
    try:
        start = date.fromisoformat(request.start)
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # Default to previous calendar day: today's daily bar may be incomplete.
        end = date.fromisoformat(request.end) if request.end else today - timedelta(days=1)
    except ValueError:
        raise ValueError("下載日期格式必須為 YYYY-MM-DD") from None
    if start > end or end >= today:
        raise ValueError("起日不得晚於迄日；只下載昨日以前的已結束日線")
    rows = finmind("TaiwanStockPrice", data_id=request.symbol, start_date=str(start), end_date=str(end))
    if not rows:
        if previous is not None:
            return previous[0]
        raise ValueError("指定區間沒有資料；不以示範資料替代")
    frame = pd.DataFrame(rows).rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    if not {"date", "stock_id", "open", "high", "low", "close", "volume"}.issubset(frame.columns):
        raise ValueError("FinMind 回應缺少必要行情欄位；停止匯入，不推測缺值")
    if "stock_id" not in frame or not (frame["stock_id"].astype(str) == request.symbol).all():
        raise ValueError("來源回傳商品與要求不符")
    frame = frame[["date", "open", "high", "low", "close", "volume"]]
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    if parsed_dates.isna().any() or (parsed_dates.dt.date < start).any() or (parsed_dates.dt.date > end).any():
        raise ValueError("來源回傳日期超出要求區間")
    if previous:
        frame = pd.concat([previous[1], frame], ignore_index=True)
    frame = frame.sort_values("date")
    checked, quality = validate_csv(frame.to_csv(index=False))
    meta = {"symbol": request.symbol, "name": request.name, "asset_type": request.asset_type, "price_basis": "raw", "source": "FinMind/TaiwanStockPrice"}
    return storage.put_dataset(checked, meta, quality)


def refresh(identifier):
    from stocklab.models import DownloadRequest
    meta, frame = storage.get_dataset(identifier)
    if meta["source"] != "FinMind/TaiwanStockPrice":
        return meta
    next_date = date.fromisoformat(meta["last_date"]) + timedelta(days=1)
    if next_date >= datetime.now(ZoneInfo("Asia/Taipei")).date():
        return meta
    req = DownloadRequest(symbol=meta["symbol"], name=meta["name"], asset_type=meta["asset_type"], start=str(next_date))
    return download(req, (meta, frame))
