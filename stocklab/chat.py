"""Bounded, local strategy grammar. Unknown clauses never become executable jobs."""
import copy
import re
import unicodedata

from pydantic import ValidationError

from stocklab import ENGINE_VERSION, storage
from stocklab.models import Costs, JobRequest, Rule, Strategy

PARSER_VERSION = "1"
FIELD_NAMES = {"close": "收盤價", "open": "開盤價", "volume": "成交量", "rsi14": "RSI14",
               "volume_ratio20": "20日量比", "return1": "單日報酬", "return5": "5日報酬", "volatility20": "20日波動率"}
OP_NAMES = {"gt": "高於", "lt": "低於", "ge": "不低於", "le": "不高於", "cross_above": "向上突破", "cross_below": "向下跌破"}
OPERATORS = {"向上突破":"cross_above", "向下跌破":"cross_below", "突破":"cross_above", "跌破":"cross_below",
             "不低於":"ge", "不高於":"le", "大於等於":"ge", "小於等於":"le", ">=":"ge", "<=":"le",
             "高於":"gt", "低於":"lt", "大於":"gt", "小於":"lt", ">":"gt", "<":"lt"}


def field_name(value):
    return FIELD_NAMES.get(value, f"{value[3:]}日均線" if value.startswith("sma") else value)


def describe_rule(rule):
    r = rule.model_dump() if isinstance(rule, Rule) else rule
    value = field_name(r["value"]) if isinstance(r["value"], str) else f'{r["value"] * 100:g}%' if r["field"] in ("return1","return5","volatility20") else f'{r["value"]:g}'
    return field_name(r["field"]) + OP_NAMES[r["op"]] + value


def describe_strategy(strategy):
    s = Strategy(**strategy) if isinstance(strategy, dict) else strategy
    join = lambda mode: " 且 " if mode == "all" else " 或 "
    result = ["進場：" + join(s.entry_mode).join(describe_rule(r) for r in s.entry),
              "出場：" + (join(s.exit_mode).join(describe_rule(r) for r in s.exit) if s.exit else "持有至資料期末結清")]
    if s.stop_loss:
        result.append(f"前日收盤觸發停損 {s.stop_loss * 100:g}%，下一觀測日開盤執行")
    if s.max_hold:
        result.append(f"最多持有 {s.max_hold} 個觀測交易日")
    return result


def parse_field(value):
    aliases = {"收盤價":"close", "股價":"close", "價格":"close", "開盤價":"open", "成交量":"volume", "RSI":"rsi14",
               "RSI14":"rsi14", "量比":"volume_ratio20", "20日量比":"volume_ratio20", "單日報酬":"return1", "5日報酬":"return5", "20日波動率":"volatility20"}
    if value in aliases:
        return aliases[value]
    match = re.fullmatch(r"(?:(\d+)日(?:均線|線)|MA(\d+)|SMA(\d+))", value)
    if match:
        return "sma" + next(x for x in match.groups() if x)
    raise ValueError("無法辨識指標，支援 5/10/20/60/120 日均線、RSI14、價格、成交量及量比")


def parse_rule(text):
    pattern = "|".join(re.escape(x) for x in sorted(OPERATORS, key=len, reverse=True))
    match = re.fullmatch(r"(.+?)(" + pattern + r")(.+)", text)
    if not match:
        raise ValueError("請把條件寫完整，例如「20日均線高於60日均線進場」；跌破需說明比較的兩個指標")
    left, op, right = match.groups()
    field = parse_field(left)
    number = re.fullmatch(r"(-?\d+(?:\.\d+)?)(萬|億)?(%|元)?", right)
    if number:
        raw, unit, suffix = number.groups()
        value = float(raw) * ({"萬":10000,"億":100000000}.get(unit, 1))
        if suffix == "%":
            if field not in ("return1", "return5", "volatility20", "volume_ratio20"):
                raise ValueError("此指標請用數值而非百分比；RSI 例如「RSI低於30」")
            value /= 100
    else:
        value = parse_field(right)
    return Rule(field=field, op=OPERATORS[op], value=value)


def clarify(message):
    return {"ready": False, "content": message, "engine_version": ENGINE_VERSION, "parser_version": PARSER_VERSION}


def follow_up(text):
    compact = re.sub(r"\s+", "", text)
    cancel = compact in ("取消停損", "不設停損")
    match = re.fullmatch(r"(?:同上[，,、]?)?(?:幫我)?(?:把|改|調整)?(停損|本金|手續費|滑價)(?:改成|改為|設為|為)?(\d+(?:\.\d+)?)(%|萬|億|元)?", compact)
    if not cancel and not match:
        return None
    previous = next((m for m in reversed(storage.chat_history()) if m["role"]=="assistant" and m["proposal"] and m["proposal"].get("ready")),None)
    if previous is None:
        return clarify("請先給一組完整的進場與出場條件，再說要調整哪個參數。")
    proposal = copy.deepcopy(previous["proposal"])
    if proposal["engine_version"]!=ENGINE_VERSION or proposal["parser_version"]!=PARSER_VERSION:
        return clarify("上一組條件來自舊版本，請重新提供完整條件。")
    field, raw, suffix = ("停損","0",None) if cancel else match.groups()
    data = proposal["request"]
    if data["kind"]=="correlation":
        return clarify("上一組是關聯分析，不模擬停損、資金與交易費用；請先提供自訂回測條件。")
    if field=="停損":
        if data["kind"]!="backtest":
            return clarify("停損調整適用自訂回測；上一組是候選研究，請先提供自訂進出場規則。")
        if not cancel and suffix!="%":
            return clarify("請明寫停損百分比，例如「停損改成5%」。")
        data["strategy"]["stop_loss"] = None if cancel else float(raw)/100
    elif field=="本金":
        if suffix=="%":
            return clarify("本金請使用金額，例如「本金改成50萬」。")
        data["costs"]["initial_cash"] = float(raw)*{"萬":10000,"億":100000000}.get(suffix,1)
    else:
        if suffix!="%":
            return clarify("成本請明寫百分比，例如「手續費改成0.1%」。")
        data["costs"]["commission" if field=="手續費" else "slippage"] = float(raw)/100
    try:
        job = JobRequest(**data)
    except ValidationError:
        return clarify("調整數值超出支援範圍，請重新確認。")
    proposal["request"] = job.model_dump(mode="json")
    proposal["description"] = describe_strategy(job.strategy) if job.kind=="backtest" else proposal["description"]
    proposal["content"] = "已沿用上一組預覽的商品與條件，調整你指定的參數；請確認完整規則與成本後執行。"
    proposal["notes"] = [x for x in proposal["notes"] if not x.startswith("沿用訊息")]+[f"沿用訊息 #{previous['id']} 的資料版本，其餘參數保持原預覽。"]
    return proposal


def propose(request):
    work = unicodedata.normalize("NFKC", request.message).upper().strip()
    follow = follow_up(work)
    if follow is not None:
        return follow
    work = work.replace("≥", ">=").replace("≤", "<=").replace("万", "萬")
    work = re.sub(r"(?<!\d)\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)", lambda m:m[0].replace(",", ""), work)
    work = re.sub(r"\s+", " ", work)
    work = re.sub(r"^(?:(?:請|幫我|麻煩|我想要|我要|我想|幫忙))+", "", work)
    work = re.sub(r"(?:謝謝|一下)[。!！]*$", "", work)
    overrides, stop, holding = {}, None, None
    numeric = r"(\d+(?:\.\d+)?)"

    def extract(pattern, callback):
        nonlocal work
        matches = list(re.finditer(pattern, work))
        if len(matches) > 1:
            raise ValueError("同一項成本或風險條件出現多次，請保留一個明確數值")
        if matches:
            callback(matches[0])
            work = work[:matches[0].start()] + work[matches[0].end():]

    try:
        inventory = storage.datasets()
        selected = [storage.get_dataset(i)[0] for i in request.dataset_ids]
        available = {d["id"]: d for d in inventory + selected}
        for alias, symbol in (("台積電","2330"),("元大台灣50","0050")):
            work = work.replace(alias, symbol)
        for symbol in dict.fromkeys(d["symbol"] for d in available.values()):
            work = re.sub(r"(?<![A-Z0-9_])"+re.escape(symbol.upper())+r"\s+", symbol.upper()+"，", work)
        work = re.sub(r"\s+", "", work)
        work = re.sub(r"^(?:(?:請|幫我|麻煩|我想要|我要|我想|幫忙))+", "", work)
        extract(r"(?:初始)?(?:本金|資金)(?:NT\$|\$)?"+numeric+r"(萬|億)?元?", lambda m:overrides.update(initial_cash=float(m[1])*{"萬":10000,"億":100000000}.get(m[2],1)))
        for word, key in (("手續費","commission"),("滑價","slippage"),("(?:賣出稅|證交稅|交易稅|稅率)","sell_tax")):
            extract(word+numeric+"%", lambda m,k=key:overrides.update({k:float(m[1])/100}))
        extract(r"最低手續費"+numeric+"元?", lambda m:overrides.update(min_commission=float(m[1])))
        holder = {}
        extract(r"停損"+numeric+"%", lambda m:holder.update(stop=float(m[1])/100))
        extract(r"(?:最多持有|持有最多)(\d+)(?:個)?(?:交易)?(?:天|日)", lambda m:holder.update(holding=int(m[1])))
        stop, holding = holder.get("stop"), holder.get("holding")
        costs = Costs(**{**request.costs.model_dump(), **overrides})
        explicit = []
        for alias, symbol in (("台積電","2330"),("元大台灣50","0050")):
            if alias in work:
                work = work.replace(alias, symbol)
        for symbol in dict.fromkeys(d["symbol"] for d in available.values()):
            pattern = r"(?<![A-Z0-9_])"+re.escape(symbol.upper())+r"(?![A-Z0-9_])"
            if re.search(pattern, work):
                choices = [d for d in (selected or inventory) if d["symbol"] == symbol]
                if len(choices) != 1:
                    return clarify(f"{symbol} 有多個資料來源或版本，請在資料工作台只選一個，再送出相同訊息。")
                explicit.append(choices[0])
                work = re.sub(pattern, "", work)
        targets = explicit or selected
        if not targets:
            return clarify("請先下載／匯入商品資料，或在資料工作台選取資料，再輸入代號與條件；不會以示範資料代替缺少的行情。")
        if len({d["id"] for d in targets}) != len(targets):
            return clarify("請勿重複選取同一份資料。")
        kind = "backtest"
        if re.search(r"滾動(?:驗證|回測)|WALKFORWARD", work):
            kind = "walkforward"
            work = re.sub(r"滾動(?:驗證|回測)|WALKFORWARD", "", work)
        elif re.search(r"(?:搜尋|找)(?:勝率高的|進場)?條件|研究候選", work):
            kind = "research"
            work = re.sub(r"(?:搜尋|找)(?:勝率高的|進場)?條件|研究候選", "", work)
        elif re.search(r"關聯分析|相關性分析|關聯研究", work):
            kind = "correlation"
            work = re.sub(r"關聯分析|相關性分析|關聯研究", "", work)
        work = re.sub(r"^(?:回測|驗證|測試)", "", work)
        clauses = [x for x in re.split(r"[，,;；。\n、]+", work) if x]
        entry, exits, modes = [], [], {}
        buy_hold = clauses == ["買入持有"]
        if buy_hold:
            entry = [Rule(field="close",op="gt",value=0)]
            clauses = []
        for clause in clauses:
            if kind != "backtest":
                raise ValueError("候選搜尋／滾動驗證採固定均線候選；自訂條件請改用「回測」，不會忽略你另外寫的限制。")
            if clause in ("不設出場", "不設定出場", "持有到期末"):
                if exits:
                    raise ValueError("同時指定出場與不設定出場，請選一種。")
                modes["no_exit"] = True
                continue
            role = re.fullmatch(r"(.+?)(進場|買進|買入|出場|賣出)", clause)
            if not role:
                raise ValueError("有尚未理解的條件。請分別寫明進場／出場；日期範圍、盤中、放空、停利等目前不會自動推測或忽略。")
            body, side = role.groups()
            body = re.sub(r"^當", "", body)
            body = re.sub(r"(?:的時候|時|就|才|則)$", "", body)
            has_or, has_and = "或" in body, bool(re.search("且|以及|和",body))
            if has_or and has_and:
                raise ValueError("同一句混合且／或，請改成一種組合，或使用下方進階條件編輯器。")
            rules = [parse_rule(p) for p in re.split(r"或|並且|而且|以及|且|和",body)]
            key = "entry" if side in ("進場","買進","買入") else "exit"
            mode = "any" if has_or else "all"
            if key in modes and (modes[key] != mode or mode == "any"):
                raise ValueError("請將同類條件合併在一句，以「且」或「或」明確連接。")
            modes[key] = mode
            (entry if key == "entry" else exits).extend(rules)
        if modes.get("no_exit") and exits:
            raise ValueError("同時指定出場與不設定出場，請選一種。")
        if kind == "backtest" and (not entry or (not exits and not modes.get("no_exit") and not buy_hold)):
            raise ValueError("請同時提供進場和出場；若想持有至期末，請明寫「不設出場」。")
        if kind != "backtest" and (stop is not None or holding is not None):
            raise ValueError("風險條件只會用於自訂回測；候選搜尋不會套用它們，請改寫為回測。")
        if kind == "correlation" and overrides:
            raise ValueError("關聯分析不模擬資金及費用；請移除交易成本條件或改用回測。")
        if kind != "correlation" and any(d["asset_type"] not in ("stock","etf") for d in targets):
            raise ValueError("目前交易回測只支援股票／ETF；指數和期貨可改用關聯分析。")
        if kind == "correlation" and not 2 <= len(targets) <= 12:
            raise ValueError("關聯分析請選取 2 到 12 組資料。")
        strategy = Strategy(entry=entry,exit=exits,entry_mode=modes.get("entry","all"),exit_mode=modes.get("exit","all"),stop_loss=stop,max_hold=holding) if kind == "backtest" else Strategy()
        action_ids = {d["id"]:request.action_ids[d["id"]] for d in targets if d["id"] in request.action_ids} if kind != "correlation" else {}
        for d in targets:
            if d["id"] in action_ids:
                storage.get_actions(action_ids[d["id"]],d["id"])
        audit_ids={d["id"]:request.audit_ids[d["id"]] for d in targets if d["id"] in request.audit_ids} if kind!="correlation" else {}
        if kind=="correlation" and request.require_audit:
            raise ValueError("關聯分析尚未支援此資料檢查綁定，請先取消要求或改用回測。")
        job = JobRequest(kind=kind,dataset_ids=[d["id"] for d in targets],strategy=strategy,costs=costs,action_ids=action_ids,
                         walk_forward=request.walk_forward,audit_ids=audit_ids,require_audit=request.require_audit)
        from stocklab.audit import validate_job_audit
        from stocklab.engine import warmup
        for d in targets:
            _,frame=storage.get_dataset(d["id"])
            required=warmup(strategy)+2 if kind=="backtest" else 400 if kind=="research" else 120+job.walk_forward.train_bars+job.walk_forward.validation_bars+2*job.walk_forward.test_bars if kind=="walkforward" else 2
            if len(frame)<required:
                raise ValueError(f"{d['symbol']} 此設定需要至少 {required} 筆日線，目前只有 {len(frame)} 筆，請先補足資料。")
            actions=storage.get_actions(action_ids[d["id"]],d["id"]) if d["id"] in action_ids else None
            validate_job_audit(job,d["id"],d,actions)
        description = describe_strategy(strategy) if kind == "backtest" else ["固定 8 組均線候選，訓練／驗證選擇後檢查樣本外表現。"] if kind in ("research","walkforward") else ["共同觀測日期的日報酬關聯，不能視為因果。"]
        if kind=="walkforward":
            description.append(f"每段訓練 {job.walk_forward.train_bars} 日、驗證 {job.walk_forward.validation_bars} 日、測試 {job.walk_forward.test_bars} 日；各段獨立資金。")
        if request.require_audit:
            description.append("已要求指定日期與版本檢查通過；仍需處理複核旗標，不是來源完整性或未來績效認證。")
        notes = ["僅使用列出的資料範圍；訊號於收盤後判定，下個觀測日開盤成交。", "手續費、滑價與稅率請一起確認；不因聊天輸入而省略成本。"]
        if any(d["source"] == "SYNTHETIC_DEMO" for d in targets):
            notes.insert(0,"包含合成示範資料，僅測試軟體流程，不是市場證據。")
        if any(d["price_basis"] == "raw" and d["id"] not in action_ids for d in targets):
            notes.append("原始價格未搭配事件版本，不含完整股利／拆股處理。")
        if action_ids:
            notes.append("已套用列出的公司行動版本；價格條件為因果含息指數口徑，成交仍用原始價格。未保證事件完整性。")
        return {"ready":True,"content":"已整理成以下研究條件，請確認後執行；尚未開始運算。", "engine_version":ENGINE_VERSION,"parser_version":PARSER_VERSION,
                "request":job.model_dump(mode="json"),"description":description,"targets":[{k:d[k] for k in ("id","symbol","source","price_basis","first_date","last_date")} for d in targets],"notes":notes}
    except (ValueError, ValidationError) as e:
        if isinstance(e,ValidationError):
            return clarify("參數超出支援範圍，請檢查數值與指標；均線目前支援 5、10、20、60、120 日。")
        return clarify(str(e))
