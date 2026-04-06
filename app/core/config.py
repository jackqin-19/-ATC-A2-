from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "ATC A-2 Voice Module"
    app_version: str = "1.0.0"
    workspace_root: Path = Path(os.getenv("A2_WORKSPACE_ROOT", Path.cwd()))
    data_root: Path = Path(os.getenv("A2_DATA_ROOT", Path.cwd() / "storage"))
    db_path: Path = Path(os.getenv("A2_DB_PATH", Path.cwd() / "storage" / "a2.sqlite3"))
    temp_root: Path = Path(os.getenv("A2_TEMP_ROOT", Path.cwd() / "storage" / "tmp"))
    default_slice_minutes: int = int(os.getenv("A2_SLICE_MINUTES", "5"))
    default_slice_mb: int = int(os.getenv("A2_SLICE_MB", "100"))
    sync_interval_seconds: int = int(os.getenv("A2_SYNC_INTERVAL_SECONDS", "300"))
    max_download_task: int = int(os.getenv("A2_MAX_DOWNLOAD_TASK", "3"))
    max_realtime_conn: int = int(os.getenv("A2_MAX_REALTIME_CONN", "5"))


settings = Settings()
