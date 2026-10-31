"""Authenticated network entry point for a single personal research host."""
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    port: int
    origin: str
    data_home: Path
    demo_mode: bool


def settings(environ=None):
    env=os.environ if environ is None else environ
    if len(env.get("STOCKLAB_API_TOKEN",""))<32:
        raise ValueError("對外服務需設定至少32字元的隨機 STOCKLAB_API_TOKEN；請在主機平台安全設定，勿填入聊天。")
    origin=(env.get("STOCKLAB_PUBLIC_ORIGIN") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    parsed=urlsplit(origin)
    local=parsed.hostname in ("localhost","127.0.0.1","::1")
    if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or (parsed.scheme!="https" and not (local and parsed.scheme=="http")):
        raise ValueError("請設定完整 HTTPS 服務來源 STOCKLAB_PUBLIC_ORIGIN；Render 可沿用 RENDER_EXTERNAL_URL。")
    try:
        port=int(env.get("PORT",env.get("STOCKLAB_PORT","8000")))
    except ValueError:
        raise ValueError("PORT 必須為有效埠號") from None
    if not 1<=port<=65535:
        raise ValueError("PORT 必須在1至65535之間")
    if env.get("STOCKLAB_NO_WORKER")=="1":
        raise ValueError("對外研究服務需要背景 worker，不可停用")
    home=env.get("STOCKLAB_HOME")
    demo=env.get("STOCKLAB_DEMO_MODE")=="1"
    if not home:
        if not demo:
            raise ValueError("正式研究需明確設定 STOCKLAB_HOME 至持久磁碟")
        home="/tmp/stocklab-preview"
    return Settings(port,origin,Path(home).expanduser().resolve(),demo)


def main():
    config=settings()
    os.environ["STOCKLAB_PUBLIC_ORIGIN"]=config.origin
    os.environ["STOCKLAB_HOME"]=str(config.data_home)
    config.data_home.mkdir(parents=True,exist_ok=True)
    if config.demo_mode:
        from stocklab import storage
        from stocklab.__main__ import demo
        storage.init()
        demo()
    import uvicorn
    uvicorn.run("stocklab.app:app",host="0.0.0.0",port=config.port,proxy_headers=False)


if __name__=="__main__":
    main()
