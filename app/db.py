"""数据库初始化与连接管理。

这一层解决三个问题：
1. 第一次启动项目时自动建表。
2. 老版本数据库缺字段时自动补齐，避免升级后直接报错。
3. 给 Repository 层提供统一的连接获取方式，减少重复代码。
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

CREATE TABLE IF NOT EXISTS adsb_tracks (
    track_id TEXT PRIMARY KEY,
    callsign TEXT,
    location TEXT,
    altitude INTEGER,
    ground_speed INTEGER,
    heading INTEGER,
    timestamp TEXT,
    icao_code TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_adsb_callsign ON adsb_tracks(callsign);
CREATE INDEX IF NOT EXISTS idx_adsb_timestamp ON adsb_tracks(timestamp);
CREATE INDEX IF NOT EXISTS idx_adsb_icao_timestamp ON adsb_tracks(icao_code, timestamp);

CREATE TABLE IF NOT EXISTS a2_voice_track_rel (
    rel_id INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id TEXT,
    track_id TEXT,
    create_time TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rel_unique_id ON a2_voice_track_rel(unique_id);
CREATE INDEX IF NOT EXISTS idx_rel_track_id ON a2_voice_track_rel(track_id);

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
    """确保数据目录、临时目录和数据库目录存在。

    因为项目既要写 SQLite 文件，也要写音频文件和临时切片文件，
    所以在真正执行业务前先把目录准备好，避免首次写文件时报路径不存在。
    """

    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """在旧表缺失字段时执行兼容性补列。

    这是一个轻量级“数据库版本兼容”处理：
    如果用户之前已经运行过旧版本代码，这里会检查新字段是否缺失，
    缺的话再补，不强制删除旧库重建。
    """

    existing_columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """初始化数据库表结构，并写入默认系统配置。

    这里除了执行建表 SQL，还会：
    1. 给历史数据库补充 `source_url / segment_seconds / stream_format` 字段。
    2. 初始化一条系统基础配置记录，供集成接口读取和更新。
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
    """提供自动提交和关闭的数据库连接上下文。

    Repository 层通过 `with get_conn()` 使用连接，这样可以统一做到：
    - 使用前确保数据库已初始化。
    - 正常结束后自动提交。
    - 无论是否异常，最终都关闭连接。
    """

    init_db()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
