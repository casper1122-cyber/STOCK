"""Explicit cash dividend / split events, never guessed from price gaps."""
import io
from collections import defaultdict

import numpy as np
import pandas as pd
from pydantic import ValidationError

from stocklab import storage
from stocklab.models import ActionEvent


def validate_events(events, frame, meta):
    if meta["price_basis"] != "raw" or meta["asset_type"] not in ("stock", "etf"):
        raise ValueError("公司行動只可套用原始價格的股票／ETF，避免重複還原")
    valid_dates = set(frame["date"])
    parsed, seen = [], set()
    for value in events:
        event = ActionEvent(**value).model_dump(mode="json")
        key = event["ex_date"], event["kind"]
        if key in seen:
            raise ValueError("同一天同類型的公司行動不得重複，請先核對整合")
        if event["ex_date"] not in valid_dates or event["ex_date"] == frame.iloc[0]["date"]:
            raise ValueError("事件日須有價格且至少有前一筆資料，不能推測缺漏日的權利歸屬")
        seen.add(key)
        parsed.append(event)
    return sorted(parsed, key=lambda x: (x["ex_date"], x["kind"]))


def import_actions(request):
    meta, frame = storage.get_dataset(request.dataset_id)
    try:
        table = pd.read_csv(io.StringIO(request.csv_text), dtype=str, keep_default_na=False)
    except (ValueError, pd.errors.ParserError):
        raise ValueError("公司行動 CSV 無法解析") from None
    required = {"ex_date", "kind", "announced_date", "cash_per_share", "pay_date", "split_ratio"}
    if set(table.columns) != required or len(table) > 5000:
        raise ValueError("公司行動 CSV 需 ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio，最多 5000 筆")
    events = []
    try:
        for row in table.to_dict(orient="records"):
            for key in ("cash_per_share", "split_ratio", "pay_date"):
                if row[key] == "":
                    row.pop(key)
            events.append(ActionEvent(**row).model_dump(mode="json"))
        events = validate_events(events, frame, meta)
    except ValidationError as e:
        raise ValueError("公司行動欄位不符合規則：" + "; ".join(x["msg"] for x in e.errors(include_input=False))) from None
    return storage.put_actions(meta, request.source, events)


def events_by_date(events):
    result = defaultdict(list)
    for event in events:
        result[event["ex_date"]].append(event)
    return result


def signal_frame(frame, events):
    """Causal wealth-index OHLC for signals; raw frame remains the fill ledger.

    Dividend index assumes ex-day reinvestment for indicator normalization only.
    Trading cash remains unavailable until pay_date. Split volume uses old-share units.
    """
    f = frame.copy()
    f[["open", "high", "low", "close", "volume"]] = f[["open", "high", "low", "close", "volume"]].astype(float)
    mapped = events_by_date(events)
    index = float(frame.iloc[0]["close"])
    split_units = 1.0
    for i in range(len(f)):
        row = frame.iloc[i]
        today = mapped.get(str(row["date"]), [])
        ratio = np.prod([e["split_ratio"] for e in today if e["kind"] == "split"])
        cash = sum(e["cash_per_share"] for e in today if e["kind"] == "dividend")
        if i:
            index *= (float(row["close"]) * ratio + cash) / float(frame.iloc[i - 1]["close"])
        split_units *= ratio
        factor = index / float(row["close"])
        if not np.isfinite([index, split_units, factor]).all():
            raise ValueError("公司行動調整超出數值範圍，請核對事件")
        for col in ("open", "high", "low", "close"):
            f.iat[i, f.columns.get_loc(col)] = float(row[col]) * factor
        f.iat[i, f.columns.get_loc("volume")] = float(row["volume"]) / split_units
    return f
