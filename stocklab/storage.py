import hashlib
import io
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stocklab import ENGINE_VERSION


def root():
    p = Path(os.environ.get("STOCKLAB_HOME", Path(__file__).resolve().parents[1] / ".stocklab"))
    p.mkdir(parents=True, exist_ok=True)
    (p / "data").mkdir(exist_ok=True)
    return p


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    con = sqlite3.connect(root() / "lab.sqlite3", timeout=30)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        with con:
            yield con
    finally:
        con.close()


def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS datasets (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
          symbol TEXT NOT NULL, name TEXT NOT NULL, asset_type TEXT NOT NULL,
          price_basis TEXT NOT NULL, source TEXT NOT NULL, created TEXT NOT NULL,
          first_date TEXT NOT NULL, last_date TEXT NOT NULL, rows INTEGER NOT NULL,
          quality TEXT NOT NULL, filename TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
          status TEXT NOT NULL, request TEXT NOT NULL, created TEXT NOT NULL,
          started TEXT, finished TEXT, progress TEXT NOT NULL DEFAULT '{}',
          result TEXT, error TEXT, engine_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS computations (fingerprint TEXT PRIMARY KEY, result TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS action_bundles (
          id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL, dataset_fingerprint TEXT NOT NULL,
          fingerprint TEXT UNIQUE NOT NULL, source TEXT NOT NULL, events TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL,
          proposal TEXT, job_id INTEGER, created TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_dashboard_idx ON jobs(status,engine_version,id DESC);
        CREATE TABLE IF NOT EXISTS calendars (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, market TEXT NOT NULL,
          source TEXT NOT NULL, records TEXT NOT NULL, first_date TEXT NOT NULL, last_date TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_audits (
          id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
          report TEXT NOT NULL, created TEXT NOT NULL, action_id INTEGER);
        CREATE TABLE IF NOT EXISTS audit_selections (
          dataset_id INTEGER NOT NULL, action_key INTEGER NOT NULL, audit_id INTEGER NOT NULL,
          selected_at TEXT NOT NULL, PRIMARY KEY(dataset_id,action_key));
        """)
        audit_columns={r["name"] for r in c.execute("PRAGMA table_info(data_audits)")}
        if "action_id" not in audit_columns:
            c.execute("ALTER TABLE data_audits ADD COLUMN action_id INTEGER")
            c.execute("UPDATE data_audits SET action_id=json_extract(report,'$.action_id')")
        c.execute("CREATE INDEX IF NOT EXISTS audits_versions_idx ON data_audits(dataset_id,action_id,id DESC)")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def put_dataset(frame, meta, quality):
    # Preserve round-trip precision; source data must not be rounded for storage.
    text = frame.to_csv(index=False)
    fingerprint = hashlib.sha256((canonical(meta) + "\n" + text).encode()).hexdigest()
    filename = fingerprint + ".csv"
    target = root() / "data" / filename
    if not target.exists():
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as f:
            f.write(text)
            temp = f.name
        os.replace(temp, target)
    with connect() as c:
        c.execute("""INSERT OR IGNORE INTO datasets
          (fingerprint,symbol,name,asset_type,price_basis,source,created,first_date,last_date,rows,quality,filename)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, meta["symbol"], meta.get("name", ""), meta["asset_type"], meta["price_basis"], meta["source"], now(), str(frame.iloc[0]["date"]), str(frame.iloc[-1]["date"]), len(frame), canonical(quality), filename))
        row = c.execute("SELECT * FROM datasets WHERE fingerprint=?", (fingerprint,)).fetchone()
    return dataset_dict(row)


def dataset_dict(row):
    d = dict(row)
    d["quality"] = json.loads(d["quality"])
    d.pop("filename", None)
    return d


def datasets(latest=True):
    with connect() as c:
        rows = c.execute("SELECT * FROM datasets ORDER BY id DESC").fetchall()
    output, seen = [], set()
    for row in rows:
        key = (row["symbol"], row["asset_type"], row["price_basis"], row["source"])
        if latest and key in seen:
            continue
        seen.add(key)
        output.append(dataset_dict(row))
    return output


def get_dataset(identifier):
    with connect() as c:
        row = c.execute("SELECT * FROM datasets WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ValueError("找不到資料版本")
    text = (root() / "data" / row["filename"]).read_text()
    meta = {key: row[key] for key in ("symbol", "name", "asset_type", "price_basis", "source")}
    digest = hashlib.sha256((canonical(meta) + "\n" + text).encode()).hexdigest()
    if digest != row["fingerprint"]:
        raise ValueError("資料指紋不一致，檔案可能已變動；停止研究，請重新匯入可靠資料")
    frame = pd.read_csv(io.StringIO(text), float_precision="round_trip")
    if len(frame)!=row["rows"] or str(frame.iloc[0]["date"])!=row["first_date"] or str(frame.iloc[-1]["date"])!=row["last_date"]:
        raise ValueError("價格中繼資料與資料檔不一致，停止研究")
    return dataset_dict(row), frame


def enqueue(request, nonce=""):
    payload = canonical(request)
    key = hashlib.sha256((ENGINE_VERSION + nonce + payload).encode()).hexdigest()
    with connect() as c:
        existing = c.execute("SELECT * FROM jobs WHERE fingerprint=?", (key,)).fetchone()
        if existing:
            return job_dict(existing)
        c.execute("INSERT OR IGNORE INTO jobs(fingerprint,status,request,created,engine_version) VALUES (?,?,?,?,?)", (key, "queued", payload, now(), ENGINE_VERSION))
        identifier = c.execute("SELECT id FROM jobs WHERE fingerprint=?", (key,)).fetchone()[0]
    return get_job(identifier)


def job_dict(row):
    d = dict(row)
    for key in ("request", "progress", "result"):
        d[key] = json.loads(d[key]) if d[key] else None
    return d


def get_job(identifier):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ValueError("找不到工作")
    return job_dict(row)


def jobs(limit=20):
    with connect() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    # List endpoint stays small: full curves and trades are fetched on demand.
    result = []
    for row in rows:
        d = job_dict(row)
        d.pop("result")
        d["progress"] = {k: v for k, v in d["progress"].items() if k != "completed"}
        result.append(d)
    return result


def claim():
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running',started=?,error=NULL WHERE id=?", (now(), row["id"]))
    return get_job(row["id"])


def checkpoint(identifier, progress):
    with connect() as c:
        c.execute("UPDATE jobs SET progress=? WHERE id=?", (canonical(progress), identifier))


def finish(identifier, result=None, error=None):
    with connect() as c:
        c.execute("UPDATE jobs SET status=?,finished=?,result=?,error=? WHERE id=?", ("failed" if error else "done", now(), canonical(result) if result is not None else None, error, identifier))


def setting(key, value=None):
    with connect() as c:
        if value is not None:
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, canonical(value)))
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def cached_compute(kind, meta, strategy, costs, function, actions=None, parameters=None):
    key = hashlib.sha256(canonical({"engine": ENGINE_VERSION, "kind": kind, "data": meta["fingerprint"],
                                   "strategy": strategy if kind == "backtest" else None, "costs": costs,
                                   "actions": actions["fingerprint"] if actions else None, "parameters": parameters}).encode()).hexdigest()
    with connect() as c:
        row = c.execute("SELECT result FROM computations WHERE fingerprint=?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    result = function()
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO computations VALUES (?,?,?)", (key, canonical(result), now()))
    return result


def put_actions(meta, source, events):
    body = canonical(events)
    key = hashlib.sha256((meta["fingerprint"] + source + body).encode()).hexdigest()
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO action_bundles(dataset_id,dataset_fingerprint,fingerprint,source,events,created) VALUES (?,?,?,?,?,?)",
                  (meta["id"], meta["fingerprint"], key, source, body, now()))
        row = c.execute("SELECT id FROM action_bundles WHERE fingerprint=?", (key,)).fetchone()
    return get_actions(row[0], meta["id"])


def get_actions(identifier, dataset_id):
    with connect() as c:
        row = c.execute("SELECT * FROM action_bundles WHERE id=?", (identifier,)).fetchone()
    if not row or row["dataset_id"] != dataset_id:
        raise ValueError("公司行動版本與價格資料版本不符")
    value = dict(row)
    digest = hashlib.sha256((value["dataset_fingerprint"] + value["source"] + value["events"]).encode()).hexdigest()
    if digest != value["fingerprint"]:
        raise ValueError("公司行動資料指紋不符")
    value["events"] = json.loads(value["events"])
    return value


def action_bundles():
    with connect() as c:
        rows = c.execute("SELECT id,dataset_id,fingerprint,source,created,json_array_length(events) AS event_count FROM action_bundles ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def save_chat(role, content, proposal=None):
    with connect() as c:
        cursor = c.execute("INSERT INTO chat_messages(role,content,proposal,created) VALUES (?,?,?,?)", (role, content, canonical(proposal) if proposal else None, now()))
        identifier = cursor.lastrowid
    return get_chat(identifier)


def chat_dict(row):
    item = dict(row)
    item["proposal"] = json.loads(item["proposal"]) if item["proposal"] else None
    return item


def get_chat(identifier):
    with connect() as c:
        row = c.execute("SELECT * FROM chat_messages WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ValueError("找不到對話訊息")
    return chat_dict(row)


def chat_history():
    with connect() as c:
        rows = c.execute("SELECT * FROM chat_messages ORDER BY id DESC LIMIT 60").fetchall()
    return [chat_dict(r) for r in reversed(rows)]


def summary(identifier):
    job = get_job(identifier)
    result = job["result"] or {}
    outputs = list(result.get("outputs", {}).values())
    return {"id": job["id"], "status": job["status"], "kind": job["request"]["kind"],
            "engine_version": job["engine_version"], "error": job["error"],
            "succeeded": result.get("succeeded"), "failed": result.get("failed"), "errors": result.get("errors"),
            "outputs": [{"symbol": x["dataset"]["symbol"], "dataset_id": x["dataset"]["id"],
                         "source": x["dataset"]["source"], "price_basis": x["dataset"]["price_basis"],
                         "scope": "rolling_out_of_sample" if "folds" in x else "held_out_test" if "test" in x else "specified_backtest",
                         "metrics": None if "folds" in x else x.get("test", x)["metrics"],
                         "data_audit_status":x.get("data_audit",{}).get("status") if x.get("data_audit") else "not_provided",
                         "walkforward_summary": x.get("summary") if "folds" in x else None} for x in outputs[:10]],
            "omitted_outputs": max(0, len(outputs) - 10),
            "correlation_pairs": len(result.get("pairs", [])),
            "note": "精簡摘要；完整假設與成交明細見完整報告。合成資料不可作市場結論。"}
