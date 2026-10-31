"""Long-only daily cash engine. Signals at t close; fills at t+1 open."""
import operator

import numpy as np
import pandas as pd

from stocklab import ENGINE_VERSION
from stocklab.actions import events_by_date, signal_frame, validate_events
from stocklab.models import Costs, Rule, Strategy, WalkForwardConfig
from stocklab.risk import equity_statistics, trade_statistics

OPS = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
LOOKBACK = {"sma5": 5, "sma10": 10, "sma20": 20, "sma60": 60, "sma120": 120, "rsi14": 15, "volume_ratio20": 20, "return1": 2, "return5": 6, "volatility20": 21}


def features(frame):
    f = frame.copy()
    close = f["close"]
    for n in (5, 10, 20, 60, 120):
        f[f"sma{n}"] = close.rolling(n).mean()
    changes = close.diff()
    gain, loss = changes.clip(lower=0), -changes.clip(upper=0)
    for s in (gain, loss):
        if len(s) > 14:
            seed = s.iloc[1:15].mean()
            s.iloc[:14] = np.nan
            s.iloc[14] = seed
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()
    f["rsi14"] = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))
    f.loc[(avg_loss == 0) & (avg_gain > 0), "rsi14"] = 100
    f.loc[(avg_loss == 0) & (avg_gain == 0), "rsi14"] = 50
    f["return1"] = close.pct_change(fill_method=None)
    f["return5"] = close.pct_change(5, fill_method=None)
    f["volume_ratio20"] = f["volume"] / f["volume"].rolling(20).mean().replace(0, np.nan)
    f["volatility20"] = f["return1"].rolling(20).std(ddof=1) * np.sqrt(252)
    return f


def signals(f, rules, mode):
    if not rules:
        return np.zeros(len(f), dtype=bool)
    checks = []
    for rule in rules:
        left = f[rule.field]
        right = f[rule.value] if isinstance(rule.value, str) else rule.value
        mask = left.notna()
        if isinstance(right, pd.Series):
            mask &= right.notna()
        if rule.op in ("cross_above", "cross_below"):
            previous_right = right.shift(1) if isinstance(right, pd.Series) else right
            comparison = (left > right) & (left.shift(1) <= previous_right) if rule.op == "cross_above" else (left < right) & (left.shift(1) >= previous_right)
        else:
            comparison = OPS[rule.op](left, right)
        checks.append((comparison & mask).to_numpy())
    return (np.logical_and.reduce(checks) if mode == "all" else np.logical_or.reduce(checks))


def warmup(strategy):
    fields = []
    for r in strategy.entry + strategy.exit:
        fields.extend([r.field, r.value] if isinstance(r.value, str) else [r.field])
    extra = int(any(r.op.startswith("cross_") for r in strategy.entry + strategy.exit))
    return max([1] + [LOOKBACK.get(x, 1) for x in fields]) + extra


def default_tax(meta, costs):
    if costs.sell_tax is not None:
        return costs.sell_tax
    return 0.001 if meta["asset_type"] == "etf" else 0.003


def metrics(curve, trades, initial, exposed):
    equities = np.array([initial] + [x["equity"] for x in curve], dtype=float)
    if not np.isfinite(equities).all() or (equities <= 0).any():
        raise ValueError("權益已歸零、為負或超出可計算範圍；請檢查成本、資金與資料，不產生誤導績效")
    rets = np.diff(equities) / equities[:-1]
    years = max((pd.Timestamp(curve[-1]["date"]) - pd.Timestamp(curve[0]["date"])).days / 365.25, 1 / 365.25)
    total = equities[-1] / initial - 1
    std = np.std(rets, ddof=1) if len(rets) > 1 else 0
    cagr = float(np.expm1(np.log1p(total) / years)) if total > -1 else None
    if cagr is not None and not np.isfinite(cagr):
        cagr = None
    return {
        "total_return": float(total), "cagr": cagr,
        "max_drawdown": float(np.min(equities / np.maximum.accumulate(equities) - 1)),
        "sharpe": float(np.mean(rets) / std * np.sqrt(252)) if std > 1e-12 else None,
        "trades": len(trades), "win_rate": sum(t["pnl"] > 0 for t in trades) / len(trades) if trades else None,
        "exposure": exposed / len(curve), "final_equity": float(equities[-1]),
        "total_costs": float(sum(t["costs"] for t in trades)),
        "bars": len(curve), "start": curve[0]["date"], "end": curve[-1]["date"],
        **trade_statistics(trades), **equity_statistics(equities, rets, cagr),
    }


def simulate(f, meta, strategy, costs, start, end, buy_hold=False, prices=None, events=()):
    if end - start < 2:
        raise ValueError("扣除指標暖機期後，至少需要兩筆日線")
    entries = np.ones(len(f), dtype=bool) if buy_hold else signals(f, strategy.entry, strategy.entry_mode)
    exits = np.zeros(len(f), dtype=bool) if buy_hold else signals(f, strategy.exit, strategy.exit_mode)
    tax = default_tax(meta, costs)
    prices = f if prices is None else prices
    event_dates = events_by_date(events)
    cash, quantity = costs.initial_cash, 0
    trade, trades, curve = None, [], []
    receivables, ledger = [], []
    exposure = 0

    def sell(i, raw_price, reason):
        nonlocal cash, quantity, trade
        price = float(raw_price) * (1 - costs.slippage)
        gross = quantity * price
        fee = max(costs.min_commission, gross * costs.commission)
        sell_tax = gross * tax
        cash += gross - fee - sell_tax
        trade.update(exit_date=str(f.iloc[i]["date"]), exit_price=price, exit_signal_date=str(f.iloc[i - 1]["date"]) if reason != "end_of_sample_close" else None,
                     exit_reason=reason, pnl=gross - fee - sell_tax - trade["entry_cost"] + trade["dividend_amount"],
                     costs=trade["entry_fee"] + fee + sell_tax + trade["entry_slippage"] + quantity * (float(raw_price) - price))
        trades.append(trade)
        quantity, trade = 0, None

    for i in range(start, end):
        row, prev = prices.iloc[i], prices.iloc[i - 1]
        today = str(row["date"])
        had_position = bool(quantity)
        # Decide from yesterday's holdings and available information, before today's events.
        reason = None
        if had_position and not buy_hold:
            if exits[i - 1]:
                reason = "exit_rule"
            elif strategy.stop_loss and quantity * prev["close"] + trade["dividend_amount"] <= (trade["entry_cost"] - trade["entry_fee"]) * (1 - strategy.stop_loss):
                reason = "previous_close_stop"
            elif strategy.max_hold and i - trade["entry_index"] >= strategy.max_hold:
                reason = "max_hold"
        for claim in receivables[:]:
            if claim["pay_date"] <= today:
                cash += claim["amount"]
                ledger.append({"date": today, "kind": "dividend_payment", **claim})
                receivables.remove(claim)
        # Rights belong to the preceding close's holder, including one selling at today's open.
        if had_position:
            for event in event_dates.get(today, []):
                if event["kind"] == "dividend":
                    amount = quantity * event["cash_per_share"]
                    trade["dividend_amount"] += amount
                    claim = {"ex_date": today, "pay_date": event["pay_date"], "amount": float(amount), "entry_date": trade["entry_date"]}
                    if event["pay_date"] <= today:
                        cash += amount
                        ledger.append({"date": today, "kind": "dividend_payment", **claim})
                    else:
                        receivables.append(claim)
                    ledger.append({"date": today, "kind": "dividend_entitlement", "quantity": quantity, **claim})
                else:
                    new_quantity = quantity * event["split_ratio"]
                    if not np.isclose(new_quantity, round(new_quantity), rtol=0, atol=1e-8):
                        raise ValueError("拆併股產生零碎股；缺少畸零股現金處理規則，停止回測而非自動捨入")
                    ledger.append({"date": today, "kind": "split", "ratio": event["split_ratio"], "before": quantity, "after": int(round(new_quantity))})
                    quantity = int(round(new_quantity))
                    trade["quantity"] = quantity
                    trade["split_events"].append(event)
        if had_position and reason:
            sell(i, row["open"], reason)
        elif not had_position and entries[i - 1]:
            price = float(row["open"]) * (1 + costs.slippage)
            # Satisfy both percentage-fee and minimum-fee cash constraints.
            budget_units = min(cash / (price * (1 + costs.commission)), (cash - costs.min_commission) / price)
            quantity = max(0, int(budget_units // costs.lot_size) * costs.lot_size)
            if quantity:
                gross = quantity * price
                fee = max(costs.min_commission, gross * costs.commission)
                cash -= gross + fee
                trade = {"signal_date": str(prev["date"]), "entry_date": str(row["date"]), "entry_price": price,
                         "entry_raw_price": float(row["open"]), "entry_index": i, "quantity": quantity, "initial_quantity": quantity,
                         "entry_fee": fee, "entry_cost": gross + fee, "entry_slippage": quantity * (price - float(row["open"])),
                         "dividend_amount": 0.0, "split_events": []}
        if quantity:
            exposure += 1
        if i == end - 1 and quantity:
            sell(i, row["close"], "end_of_sample_close")
        pending = sum(x["amount"] for x in receivables)
        curve.append({"date": today, "equity": float(cash + quantity * row["close"] + pending),
                      "cash": float(cash), "receivables": float(pending), "quantity": quantity})
    return {"metrics": metrics(curve, trades, costs.initial_cash, exposure), "curve": curve, "trades": trades,
            "corporate_action_ledger": ledger, "pending_dividends": receivables}


def backtest(frame, meta, strategy=None, costs=None, start=None, end=None, prepared=False, actions=None, raw_frame=None):
    strategy, costs = strategy or Strategy(), costs or Costs()
    if meta["asset_type"] not in ("stock", "etf"):
        raise ValueError("交易回測只支援股票與 ETF；指數及期貨資料目前僅供關聯研究，不能套用現股引擎")
    used = {x for r in strategy.entry + strategy.exit for x in (r.field, r.value) if isinstance(x, str)}
    if used & {"volume", "volume_ratio20"} and (meta.get("quality", {}).get("volume_missing") or not frame["volume"].gt(0).any()):
        raise ValueError("策略使用成交量，但此資料沒有可用成交量")
    prices = frame if raw_frame is None else raw_frame
    events = validate_events(actions["events"], prices, meta) if actions is not None else []
    f = frame if prepared else features(signal_frame(prices, events) if events else prices)
    start, end = max(warmup(strategy), start or 0), end if end is not None else len(f)
    result = simulate(f, meta, strategy, costs, start, end, prices=prices, events=events)
    benchmark = simulate(f, meta, strategy, costs, start, end, buy_hold=True, prices=prices, events=events)
    warnings = ["研究性現股日線模型：訊號收盤後產生，下個觀測交易日開盤成交；期末按收盤價強制結清。",
                "未建模漲跌停無法成交、委託簿、成交量容量、停牌下單及盤中路徑；不含融資、融券或放空。",
                "預設賣出稅率：股票 0.3%、ETF 0.1%；特殊商品、歷史稅制與手續費折扣請自行覆寫。",
                "Sharpe 使用日報酬、年化 252 日、無風險利率 0；短樣本年化數字不穩定。",
                "未含下市完整母體及歷史成分股，存在存活者與選樣偏誤，不能推論全市場。"]
    if actions is not None:
        warnings.append("只套用提供的現金股利與拆併股事件，事件來源及完整性尚未獨立稽核；未含股票股利、減資退款或個人股利稅費。")
        warnings.append("現金股利除息日認列應收、付款日或下一觀測日入現金，未自動再投入；未付款應收包含在權益，不能提前買股。")
        warnings.append("條件的價格欄位採因果含息指數正規化，數值不等於實際元價格；成交與帳務仍使用原始價格。")
    elif meta["price_basis"] == "raw":
        warnings.append("未還原價格：未加入股利、配股或拆併股調整，報酬不是含息總報酬；除權息可能造成錯誤訊號。")
    else:
        warnings.append("還原價格由提供者聲明，尚未獨立驗證；假設可按還原 OHLC 做比例研究，股數與金額非真實成交帳務。")
    if meta["source"] == "SYNTHETIC_DEMO":
        warnings.insert(0, "合成示範資料：僅驗證軟體流程，不能用作市場結論。")
    result.update(benchmark={"metrics": benchmark["metrics"], "curve": benchmark["curve"]},
                  strategy=strategy.model_dump(), costs={**costs.model_dump(), "effective_sell_tax": default_tax(meta, costs)},
                  dataset=meta, engine_version=ENGINE_VERSION, warnings=warnings)
    result["corporate_actions"] = actions
    return result


class NoCandidateError(ValueError):
    pass


def select_candidate(f, raw, meta, costs, first, a, b, actions=None):
    """Selection sees only the specified training and validation intervals."""
    candidates = []
    for fast in (5, 10, 20):
        for slow in (20, 60, 120):
            if fast >= slow:
                continue
            strategy = Strategy(entry=[Rule(field=f"sma{fast}", op="gt", value=f"sma{slow}")], exit=[Rule(field=f"sma{fast}", op="lt", value=f"sma{slow}")])
            result = backtest(f, meta, strategy, costs, first, a, prepared=True, raw_frame=raw, actions=actions)
            m = result["metrics"]
            # At least 3 completed trades required; flat runs are not candidates.
            if m["trades"] >= 3 and m["sharpe"] is not None:
                candidates.append({"strategy": strategy.model_dump(), "train": m})
    if not candidates:
        raise NoCandidateError("訓練段沒有至少 3 筆成交且可計算 Sharpe 的候選策略；不強行選出贏家")
    candidates.sort(key=lambda x: x["train"]["sharpe"], reverse=True)
    shortlist = candidates[:3]
    for candidate in shortlist:
        candidate["validation"] = backtest(f, meta, Strategy(**candidate["strategy"]), costs, a, b, prepared=True, raw_frame=raw, actions=actions)["metrics"]
    eligible = [x for x in shortlist if x["validation"]["trades"] >= 1 and x["validation"]["sharpe"] is not None]
    if not eligible:
        raise NoCandidateError("驗證段沒有可比較的已成交候選策略；不使用測試段補選")
    chosen = max(eligible, key=lambda x: x["validation"]["sharpe"])
    return chosen, shortlist, len(candidates)


def research(frame, meta, costs=None, actions=None):
    """Train shortlist, validate selection, evaluate ONE selected model on test."""
    costs = costs or Costs()
    events = validate_events(actions["events"], frame, meta) if actions is not None else []
    f = features(signal_frame(frame, events) if events else frame)
    first, n = 120, len(f) - 120
    if n < 280:
        raise ValueError("候選條件研究至少需要 400 筆日線；較短資料仍可做指定策略回測")
    a, b = first + int(n * .6), first + int(n * .8)
    chosen, shortlist, eligible_count = select_candidate(f, frame, meta, costs, first, a, b, actions)
    heldout = backtest(f, meta, Strategy(**chosen["strategy"]), costs, b, len(f), prepared=True, raw_frame=frame, actions=actions)
    return {"dataset": meta, "engine_version": ENGINE_VERSION, "selected_strategy": chosen["strategy"],
            "candidate_count": 8, "eligible_train_count": eligible_count, "shortlist": shortlist,
            "split": {"train": [str(f.iloc[first]["date"]), str(f.iloc[a - 1]["date"])],
                      "validation": [str(f.iloc[a]["date"]), str(f.iloc[b - 1]["date"])],
                      "test": [str(f.iloc[b]["date"]), str(f.iloc[-1]["date"])]},
            "test": heldout,
            "warnings": ["探索性候選搜尋，不等於有獲利能力。固定 8 組均線候選；訓練取前 3、驗證選 1，測試段只評估該策略。",
                         "這是單次時間切分，尚非滾動 walk-forward、巢狀驗證或多重檢定校正；少量成交不構成統計證據。",
                         "反覆查看同一測試段或跨商品挑最好結果，也會污染樣本外證據；需另留真正未看過的資料。",
                         "未使用相關性自動推導交易訊號；跨市場交易時間與資料發布時點需另行建模。"]}


def walkforward(frame, meta, costs=None, config=None, actions=None):
    costs, config = costs or Costs(), config or WalkForwardConfig()
    events = validate_events(actions["events"], frame, meta) if actions is not None else []
    f = features(signal_frame(frame, events) if events else frame)
    first_test = 120 + config.train_bars + config.validation_bars
    count = (len(f) - first_test) // config.test_bars
    if count < 2:
        minimum = first_test + 2 * config.test_bars
        raise ValueError(f"此滾動設定至少需要 {minimum} 筆日線，才能有兩個完整測試段")
    if count > config.max_folds:
        raise ValueError(f"此資料需要 {count} 段，超過設定的 {config.max_folds} 段；請增加測試段長度或明確選定資料範圍")
    folds = []
    flat = Strategy(entry=[Rule(field="close", op="lt", value=0)], exit=[])
    for index in range(count):
        test_start = first_test + index * config.test_bars
        val_start = test_start - config.validation_bars
        train_start = val_start - config.train_bars
        test_end = test_start + config.test_bars
        selection_error = None
        try:
            chosen, shortlist, eligible_count = select_candidate(f, frame, meta, costs, train_start, val_start, test_start, actions)
            strategy = Strategy(**chosen["strategy"])
        except NoCandidateError as e:
            chosen, shortlist, eligible_count, strategy = None, [], 0, flat
            selection_error = str(e)
        tested = backtest(f, meta, strategy, costs, test_start, test_end, prepared=True, raw_frame=frame, actions=actions)
        folds.append({"index": index + 1, "train": [str(f.iloc[train_start]["date"]), str(f.iloc[val_start - 1]["date"])],
                      "validation": [str(f.iloc[val_start]["date"]), str(f.iloc[test_start - 1]["date"])],
                      "test_period": [str(f.iloc[test_start]["date"]), str(f.iloc[test_end - 1]["date"])],
                      "selected_strategy": chosen["strategy"] if chosen else None, "selection_error": selection_error,
                      "eligible_train_count": eligible_count, "shortlist": shortlist, "test": tested})
    returns = [x["test"]["metrics"]["total_return"] for x in folds]
    excess = [x["test"]["metrics"]["total_return"] - x["test"]["benchmark"]["metrics"]["total_return"] for x in folds]
    return {"kind": "walkforward", "dataset": meta, "engine_version": ENGINE_VERSION, "config": config.model_dump(), "folds": folds,
            "summary": {"folds": count, "selected_folds": sum(x["selected_strategy"] is not None for x in folds),
                        "positive_folds": sum(x > 0 for x in returns), "median_test_return": float(np.median(returns)),
                        "mean_test_excess_return": float(np.mean(excess)), "total_test_bars": count * config.test_bars},
            "untested_tail_bars": len(f) - first_test - count * config.test_bars,
            "warnings": ["每段只用較早的訓練及驗證資料選擇策略；測試段不重疊，不以測試績效回頭選擇條件。",
                         "每段獨立初始資金、獨立期末平倉，未把各段拼接為可直接交易的連續組合或複利績效。",
                         "沒有合格策略的測試段保留為持有現金，不刪除較差或未成交區段。",
                         "仍未完成多重檢定、信賴區間、全市場母體及獨立前瞻驗證；滾動結果不是獲利保證。"]}


def correlations(items):
    if len(items) < 2:
        raise ValueError("關聯研究至少選擇兩組資料")
    if len(items) > 12:
        raise ValueError("單次關聯研究最多 12 組資料")
    pairs = []
    series = []
    for meta, frame in items:
        s = frame.set_index("date")["close"].pct_change(fill_method=None).rename(str(meta["id"]))
        series.append(s)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            pair = pd.concat([series[i], series[j]], axis=1, join="inner").dropna()
            # Reject returns spanning unequal holiday/missing-data intervals.
            di, dj = items[i][1]["date"], items[j][1]["date"]
            pi, pj = pd.Series(di.shift().to_numpy(), index=di), pd.Series(dj.shift().to_numpy(), index=dj)
            valid = pi.reindex(pair.index) == pj.reindex(pair.index)
            pair = pair.loc[valid]
            lags = []
            for lag in range(-5, 6):
                aligned = pd.concat([pair.iloc[:, 0].shift(lag), pair.iloc[:, 1]], axis=1).dropna()
                usable = len(aligned) >= 30 and aligned.iloc[:, 0].std() > 0 and aligned.iloc[:, 1].std() > 0
                value = aligned.iloc[:, 0].corr(aligned.iloc[:, 1]) if usable else np.nan
                lags.append({"lag_common_sessions": lag, "correlation": float(value) if np.isfinite(value) else None, "observations": len(aligned)})
            pairs.append({"x": items[i][0], "y": items[j][0], "lags": lags})
    return {"pairs": pairs, "warnings": ["僅計算共同日期且報酬起訖相同的收盤價報酬；未補值。至少 30 組觀測才顯示 Pearson 相關。",
            "正 lag：X 較早、Y 較晚；負 lag 反之。單位為共同觀測日，非固定日曆日。",
            "僅供探索，不代表因果或可交易預測；未校正多重比較、時區收盤差、發布延遲與非同步交易。",
            "期貨連續序列需自行提供換月口徑；原始價格未處理除權息，可能造成虛假關聯。"], "engine_version": ENGINE_VERSION}
