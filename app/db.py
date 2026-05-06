"""数据库初始化与连接管理。

这一层解决三个问题：
1. 确保数据目录、临时目录存在。
2. 如果数据库表尚未由 Alpha A-5 创建，则按兼容模式自建。
3. 给 Repository 层提供统一的连接获取方式，减少重复代码。

说明：生产环境由 Alpha A-5 侧通过 SQLAlchemy 统一管理表结构，
但本模块保留 CREATE TABLE IF NOT EXISTS 以兼容独立运行和测试场景。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.core.config import settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS a2_voice_info (
    unique_id TEXT PRIMARY KEY,
    icao_code TEXT,
    band TEXT,
    original_time TEXT,
    process_time TEXT,
    file_path TEXT,
    file_name TEXT,
    file_size BIGINT DEFAULT 0,
    data_type TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    start_at TEXT,
    end_at TEXT,
    checksum TEXT,
    valid_status TEXT DEFAULT 'valid'
);
CREATE INDEX IF NOT EXISTS idx_voice_info_icao ON a2_voice_info(icao_code);
CREATE INDEX IF NOT EXISTS idx_voice_info_band ON a2_voice_info(band);
CREATE INDEX IF NOT EXISTS idx_voice_info_time ON a2_voice_info(original_time);
CREATE INDEX IF NOT EXISTS idx_voice_info_range ON a2_voice_info(start_at, end_at);

CREATE TABLE IF NOT EXISTS a2_task_realtime_cfg (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    server_addr TEXT,
    server_port INTEGER,
    protocol TEXT DEFAULT 'TCP',
    timeout INTEGER DEFAULT 30,
    heart_beat INTEGER DEFAULT 10,
    icao_code TEXT,
    band TEXT,
    source_url TEXT,
    segment_seconds INTEGER DEFAULT 60,
    stream_format TEXT,
    status INTEGER DEFAULT 0,
    create_time TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a2_task_download_cfg (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    icao_code TEXT,
    band TEXT,
    start_time TEXT,
    end_time TEXT,
    speed_limit INTEGER DEFAULT 0,
    exec_type INTEGER DEFAULT 1,
    exec_time TEXT DEFAULT NULL,
    status INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'medium',
    progress REAL DEFAULT 0,
    resume_from INTEGER DEFAULT 0,
    create_time TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a2_sys_base_cfg (
    id INTEGER PRIMARY KEY,
    storage_root TEXT DEFAULT '/atc/a2/data/',
    slice_rule TEXT DEFAULT '5min/100MB',
    max_download_task INTEGER DEFAULT 3,
    max_realtime_conn INTEGER DEFAULT 5,
    api_timeout INTEGER DEFAULT 5,
    sync_interval INTEGER DEFAULT 5,
    update_time TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_dirs() -> None:
    """确保数据目录、临时目录和数据库目录存在。"""

    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """在旧表缺失字段时执行兼容性补列。"""

    existing_columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """初始化数据库环境并写入默认系统配置。

    使用 CREATE TABLE IF NOT EXISTS，这样如果 Alpha 已建表就是空操作，
    如果 ATC-A2 独立运行也能自建表兼容。
    """

    ensure_dirs()
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        ensure_column(conn, "a2_task_realtime_cfg", "source_url", "TEXT")
        ensure_column(conn, "a2_task_realtime_cfg", "segment_seconds", "INTEGER DEFAULT 60")
        ensure_column(conn, "a2_task_realtime_cfg", "stream_format", "TEXT")
        conn.execute(
            """
            INSERT INTO a2_sys_base_cfg (
                id, storage_root, slice_rule, max_download_task, max_realtime_conn, api_timeout, sync_interval
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                1,
                str(settings.data_root),
                f"{settings.default_slice_minutes}min/{settings.default_slice_mb}MB",
                settings.max_download_task,
                settings.max_realtime_conn,
                5,
                max(1, settings.sync_interval_seconds // 60),
            ),
        )
        conn.commit()


@contextmanager
def get_conn():
    """提供自动提交和关闭的数据库连接上下文。"""

    init_db()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
