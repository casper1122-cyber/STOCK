"""Consistent SQLite online backup plus immutable data files; no secret files."""
import json
import os
import re
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from stocklab import ENGINE_VERSION, storage


def create_backup(output=None):
    directory = storage.root() / "backups"
    directory.mkdir(exist_ok=True)
    target = Path(output) if output else directory / ("stocklab-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".tar.gz")
    if target.exists():
        raise ValueError("備份檔案已存在，不覆寫")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        snapshot = Path(temp) / "lab.sqlite3"
        with storage.connect() as source, closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
            destination.commit()
        with closing(sqlite3.connect(snapshot)) as c:
            names = [r[0] for r in c.execute("SELECT filename FROM datasets")]
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("資料庫完整性檢查失敗，未建立備份")
        if not all(re.fullmatch(r"[0-9a-f]{64}\.csv", name) for name in names):
            raise ValueError("不合法的資料檔案名稱")
        manifest = Path(temp) / "manifest.json"
        manifest.write_text(json.dumps({"engine_version": ENGINE_VERSION, "created": storage.now(), "dataset_files": len(names)}, indent=2))
        archive = Path(temp) / "snapshot.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snapshot, arcname="lab.sqlite3")
            tar.add(manifest, arcname="manifest.json")
            for name in names:
                tar.add(storage.root() / "data" / name, arcname="data/" + name)
        # Copy into the target filesystem first, then atomically publish the completed archive.
        import shutil
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
            staging = Path(f.name)
        try:
            shutil.copyfile(archive, staging)
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    return target


def restore(archive, destination):
    dest = Path(destination)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("只能還原到全新或空的資料目錄；不覆寫現有研究")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or not {"lab.sqlite3", "manifest.json"}.issubset(names):
            raise ValueError("備份缺少必要檔案或包含重複項目")
        for m in members:
            if not m.isfile() or not (m.name in ("lab.sqlite3", "manifest.json") or re.fullmatch(r"data/[0-9a-f]{64}\.csv", m.name)):
                raise ValueError("備份包含不安全路徑或不支援的檔案")
        dest.mkdir(parents=True, exist_ok=True)
        tar.extractall(dest, filter="data")
    with closing(sqlite3.connect(dest / "lab.sqlite3")) as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("還原後資料庫完整性檢查失敗，請勿啟動")
        c.row_factory = sqlite3.Row
        required = c.execute("SELECT * FROM datasets").fetchall()
    if any(not re.fullmatch(r"[0-9a-f]{64}\.csv", row["filename"]) for row in required):
        raise ValueError("備份資料庫含不合法資料路徑，請勿啟動")
    if any(not (dest / "data" / row["filename"]).is_file() for row in required):
        raise ValueError("備份缺少資料檔案，請勿啟動")
    import hashlib
    for row in required:
        meta = {key: row[key] for key in ("symbol", "name", "asset_type", "price_basis", "source")}
        text = (dest / "data" / row["filename"]).read_text()
        fingerprint = hashlib.sha256((storage.canonical(meta) + "\n" + text).encode()).hexdigest()
        if fingerprint != row["fingerprint"]:
            raise ValueError("還原資料指紋不符，請勿啟動")
    return dest
