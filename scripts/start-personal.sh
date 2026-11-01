#!/usr/bin/env bash
# Personal Linux/WSL host; loopback only. No hosting subscription.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ ! -x .venv/bin/python ]; then
  echo '請先執行 bash scripts/setup.sh'
  exit 1
fi
exec .venv/bin/python - <<'PY'
import os
import secrets
from pathlib import Path

home=Path(os.environ.get("STOCKLAB_HOME", ".stocklab")).expanduser().resolve()
home.mkdir(parents=True,exist_ok=True)
if not os.environ.get("STOCKLAB_API_TOKEN"):
    token_file=home / "access-token"
    try:
        descriptor=os.open(token_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor,"w") as stream:
            stream.write(secrets.token_urlsafe(36)+"\n")
    token_file.chmod(0o600)
    os.environ["STOCKLAB_API_TOKEN"]=token_file.read_text().strip()
    print("通行碼保存在本機資料目錄的 access-token；請在自己的電腦查看並輸入手機，勿貼到聊天。",flush=True)
os.environ["STOCKLAB_HOME"]=str(home)
os.environ.setdefault("PORT",os.environ.get("STOCKLAB_PORT","8000"))
os.environ.setdefault("STOCKLAB_PUBLIC_ORIGIN","http://127.0.0.1:"+os.environ["PORT"])
os.environ.pop("STOCKLAB_EPHEMERAL_MODE",None)
os.environ.pop("STOCKLAB_DEMO_MODE",None)
from stocklab.production import settings
config=settings()
print("個人電腦模式：資料保存在本機。請保持電腦與程式執行；瀏覽器可以關閉。",flush=True)
import uvicorn
uvicorn.run("stocklab.app:app",host="127.0.0.1",port=config.port,proxy_headers=False)
PY
