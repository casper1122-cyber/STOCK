"""Persistent single-worker queue. No AI API, no token usage per research run."""
import fcntl
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from stocklab import ENGINE_VERSION, storage
from stocklab.data import refresh
from stocklab.engine import backtest, correlations, research, walkforward
from stocklab.models import JobRequest, Schedule
from stocklab.audit import validate_job_audit

log = logging.getLogger(__name__)


def execute(job):
    if job["engine_version"] != ENGINE_VERSION:
        raise ValueError("引擎版本已更新；保留舊結果，請以新版本重新提交工作，避免混用舊 checkpoint")
    request = JobRequest(**job["request"])
    if request.kind == "correlation":
        return correlations([storage.get_dataset(i) for i in request.dataset_ids])
    completed = job["progress"].get("completed", {})
    errors = {}
    for identifier in request.dataset_ids:
        key = str(identifier)
        if key in completed:
            continue
        try:
            meta, frame = storage.get_dataset(identifier)
            actions = storage.get_actions(request.action_ids[identifier], identifier) if identifier in request.action_ids else None
            audit = validate_job_audit(request,identifier,meta,actions)
            if request.kind == "daily":
                # Use newest matching immutable revision when updating a scheduled dataset.
                newest = next((x for x in storage.datasets() if all(x[k] == meta[k] for k in ("symbol", "asset_type", "price_basis", "source"))), meta)
                meta = refresh(newest["id"])
                meta, frame = storage.get_dataset(meta["id"])
            kind = request.kind if request.kind in ("backtest", "walkforward") else "research"
            def calculate():
                if kind == "backtest":
                    result=backtest(frame, meta, request.strategy, request.costs, actions=actions)
                elif kind == "walkforward":
                    result=walkforward(frame, meta, request.costs, request.walk_forward, actions)
                else:
                    result=research(frame, meta, request.costs, actions)
                result["data_audit"]=audit
                return result
            output = storage.cached_compute(kind, meta, request.strategy.model_dump(), request.costs.model_dump(),
                calculate, actions=actions, parameters={"walk_forward":request.walk_forward.model_dump() if kind=="walkforward" else None,"audit":audit["fingerprint"] if audit else None})
            completed[key] = output
        except ValueError as e:
            errors[key] = str(e)
        storage.checkpoint(job["id"], {"completed": completed, "errors": errors, "processed": len(completed) + len(errors), "total": len(request.dataset_ids)})
    return {"kind": request.kind, "outputs": completed, "errors": errors,
            "succeeded": len(completed), "failed": len(errors)}


def scheduled(now=None):
    config = storage.setting("schedule")
    if not config:
        return
    schedule = Schedule(**config)
    local = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if not schedule.enabled or local.weekday() >= 5 or (local.hour, local.minute) < (schedule.hour, schedule.minute):
        return
    req = JobRequest(kind="daily", dataset_ids=schedule.dataset_ids, costs=schedule.costs)
    storage.enqueue(req.model_dump(), nonce="schedule:" + local.date().isoformat())


def run(once=False):
    storage.init()
    # One owner across API servers and CLI invocations. Requeue only after ownership.
    with (storage.root() / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if once:
                raise RuntimeError("背景 worker 已執行中；請由 APP 提交工作") from None
            return
        with storage.connect() as c:
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        while True:
            scheduled()
            job = storage.claim()
            if job:
                try:
                    result = execute(job)
                    error = "所有選定商品均未完成；請查閱個別錯誤" if result.get("succeeded") == 0 else None
                    storage.finish(job["id"], result, error)
                except ValueError as e:
                    storage.finish(job["id"], error=str(e))
                except Exception:
                    # Never persist exception bodies that could contain request credentials.
                    log.exception("Job %s failed unexpectedly", job["id"])
                    storage.finish(job["id"], error="程式發生未預期錯誤，請檢查主機日誌")
                from stocklab.__main__ import context
                context(emit=False)
                if job["request"]["kind"] == "daily" and (storage.setting("schedule") or {}).get("backup", False):
                    try:
                        from stocklab.backup import create_backup
                        create_backup()
                        backups = sorted((storage.root() / "backups").glob("stocklab-*.tar.gz"))
                        for path in backups[:-7]:
                            path.unlink()
                        storage.setting("backup_status", {"last_success": storage.now(), "error": None})
                    except Exception:
                        storage.setting("backup_status", {"error": "本地備份失敗，請檢查磁碟與主機日誌"})
                        log.exception("Local backup failed")
            if once:
                return
            if not job:
                time.sleep(2)


if __name__ == "__main__":
    run()
