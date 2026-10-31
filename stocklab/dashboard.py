"""Compact research views. Candidate ordering never uses held-out test outcomes."""
import json

from stocklab import ENGINE_VERSION, storage
from stocklab.chat import describe_strategy
from stocklab.risk import wilson_interval
from stocklab.audit import audits


def research_dashboard(min_trades=10, include_demo=False):
    inventory = storage.datasets()
    current_checks={(a["dataset_id"],a["action_id"]):a["status"] for a in audits()}
    with storage.connect() as c:
        counts = {r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status")}
        rows = c.execute("""SELECT j.id job_id, j.request,
          json_extract(o.value,'$.dataset') dataset,
          json_extract(o.value,'$.shortlist') shortlist,
          json_extract(o.value,'$.split') split,
          json_extract(o.value,'$.selected_strategy') selected_strategy,
          json_extract(o.value,'$.test.metrics') test_metrics,
          json_extract(o.value,'$.test.corporate_actions.fingerprint') actions_fingerprint
          ,json_extract(o.value,'$.data_audit.status') audit_status
          ,json_extract(o.value,'$.test.corporate_actions.id') action_id
          FROM jobs j, json_each(j.result,'$.outputs') o
          WHERE j.status='done' AND j.engine_version=? AND json_extract(j.request,'$.kind')='research'
          ORDER BY j.id DESC LIMIT 60""", (ENGINE_VERSION,)).fetchall()
    candidates, seen = [], set()
    hidden_demo, insufficient, blocked_audits = 0, 0, 0
    for row in rows:
        meta = json.loads(row["dataset"])
        if row["audit_status"]=="issues_found" or current_checks.get((meta["id"],row["action_id"]))=="issues_found":
            blocked_audits+=1
            continue
        if meta["source"] == "SYNTHETIC_DEMO" and not include_demo:
            hidden_demo += 1
            continue
        split = json.loads(row["split"])
        request = json.loads(row["request"])
        chosen = json.loads(row["selected_strategy"])
        for item in json.loads(row["shortlist"] or "[]"):
            train, validation = item["train"], item.get("validation")
            if not validation or validation["win_rate"] is None:
                continue
            key = storage.canonical({"data":meta["fingerprint"],"actions":row["actions_fingerprint"],
                                     "strategy":item["strategy"],"costs":request["costs"],"validation":split["validation"]})
            if key in seen:
                continue
            seen.add(key)
            if validation["trades"] < min_trades or train["trades"] < min_trades:
                insufficient += 1
                continue
            candidates.append({"job_id":row["job_id"],"dataset_id":meta["id"],"symbol":meta["symbol"],
                               "source":meta["source"],"price_basis":meta["price_basis"],"fingerprint":meta["fingerprint"],
                               "data_audit_status":row["audit_status"] or "not_provided",
                               "description":describe_strategy(item["strategy"]),"strategy":item["strategy"],
                               "train":train,"validation":validation,"periods":split,"costs":request["costs"],
                               "win_rate_interval95":wilson_interval(validation.get("winning_trades",round(validation["win_rate"]*validation["trades"])),validation["trades"]),
                               "selected_in_original_study":item["strategy"]==chosen,
                               "heldout_test":json.loads(row["test_metrics"]) if item["strategy"]==chosen else None})
    candidates.sort(key=lambda x:(x["validation"]["win_rate"],x["validation"]["trades"]),reverse=True)
    return {"engine_version":ENGINE_VERSION,"candidates":candidates[:8],"eligible_candidates":len(candidates),
            "insufficient_samples":insufficient,"hidden_synthetic_studies":hidden_demo,"min_trades":min_trades,
            "studies_with_failed_audits":blocked_audits,
            "studies_examined":len(rows),"counts":counts,
            "inventory":{"datasets":len(inventory),"rows":sum(x["rows"] for x in inventory),
                         "non_demo_datasets":sum(x["source"]!="SYNTHETIC_DEMO" for x in inventory),
                         "synthetic_datasets":sum(x["source"]=="SYNTHETIC_DEMO" for x in inventory)},
            "notes":["只檢視目前引擎最近 60 組候選研究；依驗證段勝率排序，訓練與驗證段皆須達成交筆數門檻。",
                     "勝率高仍可能虧損；請同看淨報酬、回撤、成本與樣本數。不是全市場最優條件。",
                     "測試段僅顯示原研究已選策略的結果，不參與此頁排序；查看後再挑策略仍會造成樣本外選擇偏誤。",
                     "合成資料預設隱藏；來源與公司行動完整性尚需稽核。"]}
