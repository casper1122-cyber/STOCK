import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from stocklab import storage
from stocklab.data import validate_csv


def demo():
    rng = np.random.default_rng(9527)
    dates = pd.bdate_range("2020-01-01", periods=1000).strftime("%Y-%m-%d")
    market = rng.normal(.00025, .013, len(dates))
    output = []
    for symbol, ret, kind in (("DEMO_STOCK", market, "stock"), ("DEMO_ETF", .7 * market + rng.normal(0, .005, len(dates)), "etf")):
        close = 100 * np.exp(np.cumsum(ret))
        opening = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, .003, len(dates)))
        frame = pd.DataFrame({"date": dates, "open": opening, "high": np.maximum(opening, close) * 1.005,
                              "low": np.minimum(opening, close) * .995, "close": close,
                              "volume": rng.integers(100000, 5000000, len(dates))})
        frame, quality = validate_csv(frame.to_csv(index=False))
        output.append(storage.put_dataset(frame, {"symbol": symbol, "name": "合成示範，非市場資料", "asset_type": kind, "price_basis": "raw", "source": "SYNTHETIC_DEMO"}, quality))
    # Small, hand-reconcilable dividend/split fixture for the optional browser test.
    p = np.array([100, 100, 90, 90, 45, 45, 45], dtype=float)
    frame = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=len(p)).strftime("%Y-%m-%d"),
                          "open": p, "high": p, "low": p, "close": p, "volume": np.full(len(p), 10000.)})
    meta = {"symbol": "DEMO_ACTIONS", "name": "合成股利拆股帳務測試，非市場資料", "asset_type": "stock", "price_basis": "raw", "source": "SYNTHETIC_DEMO"}
    dataset = storage.put_dataset(frame, meta, {"calendar_verified": False, "corporate_actions_verified": False, "volume_missing": False})
    output.append(dataset)
    csv = "ex_date,kind,announced_date,cash_per_share,pay_date,split_ratio\n2020-01-03,dividend,2020-01-01,10,2020-01-08,\n2020-01-07,split,2020-01-01,,,2\n"
    directory = storage.root() / "verification"
    directory.mkdir(exist_ok=True)
    (directory / "phase2-fixture.json").write_text(json.dumps({"id": dataset["id"], "csv": csv}))
    return output


def context(emit=True):
    data = storage.datasets()
    jobs = storage.jobs(5)
    from stocklab import ENGINE_VERSION
    summary = [f"# 本地研究摘要 / 引擎 {ENGINE_VERSION}", "", "先讀 README.md、docs/STATUS.md。資料與結果不需要整份送進聊天。", "",
               "## 資料（最多列出 30 組）"]
    for x in data[:30]:
        summary.append(f'- #{x["id"]} {x["symbol"]} {x["asset_type"]} {x["first_date"]}..{x["last_date"]} {x["rows"]} rows / {x["price_basis"]} / {x["source"]}')
    summary.extend(["", "## 公司行動版本（最多 10 組）"])
    for x in storage.action_bundles()[:10]:
        summary.append(f'- 事件 #{x["id"]} → 資料 #{x["dataset_id"]}; {x["event_count"]} events / {x["source"]}')
    from stocklab.audit import audits,calendars
    checked=audits()
    summary.extend(["", "## 資料檢查摘要", f'日曆版本 {len(calendars())} 組；目前明確選定／可用檢查 {len(checked)} 組，其中問題狀態 {sum(x["status"]=="issues_found" for x in checked)} 組。指定檢查通過不等於來源獨立驗證。'])
    summary.extend(["", "## 最近五個工作"])
    for j in jobs:
        summary.append(f'- #{j["id"]} {j["request"]["kind"]} {j["status"]}; dataset_ids={j["request"]["dataset_ids"]}; error={j["error"]}')
    summary.extend(["", "下載、回測及排程不呼叫 LLM。未完成能力與資料缺口見 docs/DATA_SOURCES.md。"])
    text = "\n".join(summary) + "\n"
    (storage.root() / "CONTEXT.md").write_text(text)
    if emit:
        print(text)


def main():
    parser = argparse.ArgumentParser(description="台股研究室")
    parser.add_argument("command", choices=["serve", "demo", "worker", "context", "backup", "restore"])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output")
    parser.add_argument("--archive")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.command == "restore":
        if not args.archive or not args.destination:
            parser.error("restore 需要 --archive 與 --destination")
        from stocklab.backup import restore
        print(restore(args.archive, args.destination))
        return
    storage.init()
    if args.command == "demo":
        print(json.dumps(demo(), ensure_ascii=False, indent=2))
    elif args.command == "context":
        context()
    elif args.command == "worker":
        from stocklab.worker import run
        run(once=args.once)
    elif args.command == "backup":
        from stocklab.backup import create_backup
        print(create_backup(args.output))
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("stocklab.app:app", host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
