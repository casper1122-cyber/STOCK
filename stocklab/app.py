import hmac
import io
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from stocklab import ENGINE_VERSION, storage
from stocklab.data import download, import_csv
from stocklab.actions import import_actions
from stocklab.models import ActionsImport, AuditRequest, CalendarImport, ChatRequest, DownloadRequest, ImportRequest, JobRequest, Schedule
from stocklab.chat import PARSER_VERSION, propose
from stocklab.dashboard import research_dashboard
from stocklab.audit import audits, calendars, get_audit, import_calendar, run_audit, validate_job_audit

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    storage.init()
    worker = None
    if os.environ.get("STOCKLAB_NO_WORKER") != "1":
        worker = subprocess.Popen([sys.executable, "-m", "stocklab.worker"], cwd=Path(__file__).resolve().parents[1])
    app.state.worker=worker
    yield
    if worker and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


app = FastAPI(title="台股研究室", version=ENGINE_VERSION, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        token = os.environ.get("STOCKLAB_API_TOKEN")
        if token and not hmac.compare_digest(request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()):
            return JSONResponse({"detail": "請先輸入研究室通行碼"}, status_code=401,headers={"Cache-Control":"no-store"})
        if os.environ.get("STOCKLAB_DEMO_MODE")=="1" and request.method in ("POST","PUT") and request.url.path in ("/api/import","/api/download","/api/actions","/api/calendars","/api/schedule"):
            return JSONResponse({"detail":"目前是免費體驗模式，只使用合成示範資料；正式研究需有持久磁碟，才能匯入行情及啟用排程。"},status_code=409,headers={"Cache-Control":"no-store"})
        # Browser writes must originate from this app; no cross-site form submissions.
        origin = request.headers.get("origin")
        permitted_origins = {str(request.base_url).rstrip("/")}
        public_origin = os.environ.get("STOCKLAB_PUBLIC_ORIGIN", "").rstrip("/")
        if public_origin:
            permitted_origins.add(public_origin)
        if request.method not in ("GET", "HEAD") and origin and origin not in permitted_origins:
            return JSONResponse({"detail": "不接受跨來源寫入"}, status_code=403)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "無效的 Content-Length"}, status_code=400)
        if size > 9_000_000:
            return JSONResponse({"detail": "單次上傳限制 9 MB"}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def health():
    with storage.connect() as c:
        c.execute("SELECT 1").fetchone()
    worker=getattr(app.state,"worker",None)
    if worker is not None and worker.poll() is not None:
        return JSONResponse({"status":"worker_unavailable"},status_code=503)
    return {"status":"ok","engine_version":ENGINE_VERSION}


@app.get("/app-info")
def app_info():
    return JSONResponse({"requires_key":bool(os.environ.get("STOCKLAB_API_TOKEN")),"demo_mode":os.environ.get("STOCKLAB_DEMO_MODE")=="1"},headers={"Cache-Control":"no-store"})


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(STATIC / "sw.js",media_type="application/javascript",headers={"Cache-Control":"no-cache","Service-Worker-Allowed":"/"})


@app.get("/api/status")
def status():
    return {"engine_version": ENGINE_VERSION, "datasets": len(storage.datasets()), "llm_calls": 0,
            "timezone": "Asia/Taipei", "mode": "研究開發版，非專業完整市場資料庫",
            "finmind_token_present": bool(os.environ.get("FINMIND_TOKEN"))}


@app.get("/api/datasets")
def list_datasets():
    return storage.datasets()


@app.get("/api/calendars")
def list_calendars():
    return calendars()


@app.post("/api/calendars")
def calendar_import(request: CalendarImport):
    return import_calendar(request)


@app.get("/api/audits")
def list_audits():
    return audits()


@app.post("/api/audits")
def audit_create(request: AuditRequest):
    return run_audit(request)


@app.get("/api/audits/{identifier}")
def audit_get(identifier: int):
    return get_audit(identifier)


@app.get("/api/audits/{identifier}/export")
def audit_export(identifier: int):
    return Response(json.dumps(get_audit(identifier),ensure_ascii=False,indent=2),media_type="application/json",headers={"Content-Disposition":f'attachment; filename="data-audit-{identifier}.json"'})


@app.get("/api/dashboard")
def dashboard(min_trades: int = 10, include_demo: bool = False):
    if not 1 <= min_trades <= 1000:
        raise ValueError("最低交易筆數需為 1 至 1000")
    return research_dashboard(min_trades,include_demo)


@app.get("/api/chat")
def history():
    return storage.chat_history()


@app.post("/api/chat")
def chat(request: ChatRequest):
    user = storage.save_chat("user",request.message)
    proposal = propose(request)
    assistant = storage.save_chat("assistant",proposal["content"],proposal)
    return {"user":user,"assistant":assistant}


@app.post("/api/chat/{identifier}/run")
def chat_run(identifier: int):
    message = storage.get_chat(identifier)
    if message["job_id"] is not None:
        return storage.get_job(message["job_id"])
    proposal = message["proposal"]
    if message["role"]!="assistant" or not proposal or not proposal.get("ready"):
        raise ValueError("這則訊息仍需要補充，不能執行")
    if proposal["engine_version"] != ENGINE_VERSION or proposal["parser_version"] != PARSER_VERSION:
        raise ValueError("條件解析版本已更新，請重新送出文字以確認")
    job = queue(JobRequest(**proposal["request"]))
    with storage.connect() as c:
        c.execute("UPDATE chat_messages SET job_id=? WHERE id=? AND job_id IS NULL",(job["id"],identifier))
    return job


@app.post("/api/import")
def upload(request: ImportRequest):
    return import_csv(request)


@app.post("/api/download")
def fetch(request: DownloadRequest):
    return download(request)


@app.get("/api/actions")
def actions_list():
    return storage.action_bundles()


@app.post("/api/actions")
def actions_import(request: ActionsImport):
    return import_actions(request)


@app.get("/api/actions/{identifier}")
def actions_get(identifier: int, dataset_id: int):
    return storage.get_actions(identifier, dataset_id)


@app.get("/api/datasets/{identifier}/csv")
def dataset_csv(identifier: int):
    meta, frame = storage.get_dataset(identifier)
    return Response(frame.to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="dataset-{identifier}.csv"'})


@app.get("/api/jobs")
def list_jobs():
    return storage.jobs()


@app.post("/api/jobs")
def queue(request: JobRequest):
    if request.kind == "correlation" and not 2 <= len(request.dataset_ids) <= 12:
        raise ValueError("關聯研究需選擇 2 至 12 組資料")
    if len(set(request.dataset_ids)) != len(request.dataset_ids):
        raise ValueError("不可重複選擇資料")
    for identifier in request.dataset_ids:
        meta, frame = storage.get_dataset(identifier)
        if request.kind != "correlation" and meta["asset_type"] not in ("stock", "etf"):
            raise ValueError("指數與期貨僅能執行關聯研究")
        actions=storage.get_actions(request.action_ids[identifier],identifier) if identifier in request.action_ids else None
        validate_job_audit(request,identifier,meta,actions)
    if request.kind == "daily":
        from datetime import datetime
        from zoneinfo import ZoneInfo
        nonce = "manual-daily:" + datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    else:
        nonce = ""
    return storage.enqueue(request.model_dump(), nonce=nonce)


@app.get("/api/jobs/{identifier}")
def job(identifier: int):
    return storage.get_job(identifier)


@app.post("/api/jobs/{identifier}/retry")
def retry(identifier: int):
    job = storage.get_job(identifier)
    if job["status"] != "failed":
        raise ValueError("僅能重試失敗工作")
    if job["engine_version"] != ENGINE_VERSION:
        raise ValueError("此工作使用舊引擎版本，請重新提交")
    with storage.connect() as c:
        c.execute("UPDATE jobs SET status='queued',error=NULL,finished=NULL WHERE id=? AND status='failed'", (identifier,))
    return storage.get_job(identifier)


@app.get("/api/jobs/{identifier}/export")
def export(identifier: int):
    job = storage.get_job(identifier)
    return Response(json.dumps(job, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="research-{identifier}.json"'})


@app.get("/api/jobs/{identifier}/summary")
def summary(identifier: int):
    return storage.summary(identifier)


@app.get("/api/schedule")
def get_schedule():
    return {**(storage.setting("schedule") or Schedule().model_dump()), "backup_status": storage.setting("backup_status")}


@app.put("/api/schedule")
def set_schedule(request: Schedule):
    for identifier in request.dataset_ids:
        meta, _ = storage.get_dataset(identifier)
        if meta["asset_type"] not in ("stock", "etf"):
            raise ValueError("每日研究排程只支援股票與 ETF")
    storage.setting("schedule", request.model_dump())
    return request


app.mount("/static", StaticFiles(directory=STATIC), name="static")
