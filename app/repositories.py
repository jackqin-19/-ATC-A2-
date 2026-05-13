"""数据库访问层。

这一层的目标是把“业务含义”和“SQL 细节”隔离开。
上层 Service 只关心“创建任务”“查重叠语音”“更新进度”这些动作，
不需要知道表名、字段名和 SQL 拼接细节，这样代码层次会更清晰。
"""

from __future__ import annotations

from typing import Any

from app.db import get_conn
from app.schemas import (
    A2SystemConfigUpdateRequest,
    DownloadTaskCreate,
    IntegrationDownloadTaskUpsertRequest,
    IntegrationRealtimeTaskUpsertRequest,
    RealtimeTaskCreate,
    VoiceRecord,
)


class VoiceRepository:
    """负责语音元数据及语音和航迹关系的数据库操作。"""

    def insert_voice_record(self, record: VoiceRecord) -> None:
        """写入或覆盖一条语音元数据记录。"""

        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO a2_voice_info (
                    unique_id, icao_code, band, original_time, process_time, file_path,
                    file_name, file_size, data_type, start_at, end_at, checksum, valid_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.unique_id,
                    record.icao_code,
                    record.band,
                    record.original_time,
                    record.process_time,
                    record.file_path,
                    record.file_name,
                    record.file_size,
                    record.data_type,
                    record.start_at,
                    record.end_at,
                    record.checksum,
                    record.valid_status,
                ),
            )

    def query_voice_records(
        self,
        start_time: str,
        end_time: str,
        icao_code: str | None,
        band: str | None,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """按时间重叠条件查询语音记录，并支持分页。

        这里最关键的不是“时间相等”，而是“时间重叠”：
        - `start_at < 查询结束`
        - `end_at > 查询开始`

        因为用户查询的时间窗口通常不会刚好和切片边界完全对齐，
        所以必须找出所有有重叠关系的片段，才能保证后续导出的语音完整。
        """

        # 只返回有效记录，并基于时间重叠关系做过滤。
        filters = ["start_at < ?", "end_at > ?", "valid_status = 'valid'"]
        params: list[Any] = [end_time, start_time]
        if icao_code:
            filters.append("icao_code = ?")
            params.append(icao_code)
        if band:
            filters.append("band = ?")
            params.append(band)

        where_sql = " AND ".join(filters)
        with get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(1) FROM a2_voice_info WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM a2_voice_info
                WHERE {where_sql}
                ORDER BY start_at ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page_num - 1) * page_size]),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def query_overlapping_segments(
        self, start_time: str, end_time: str, icao_code: str, band: str
    ) -> list[dict[str, Any]]:
        """查询与目标时间段有重叠的所有语音片段。

        这个方法主要给音频裁剪和拼接逻辑使用，所以不分页，
        需要一次性拿到完整的命中片段集合。
        """

        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM a2_voice_info
                WHERE start_at < ? AND end_at > ? AND icao_code = ? AND band = ? AND valid_status = 'valid'
                ORDER BY start_at ASC
                """,
                (end_time, start_time, icao_code, band),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_voice_by_unique_id(self, unique_id: str) -> dict[str, Any] | None:
        """根据唯一标识获取单条语音记录。"""

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_voice_info WHERE unique_id = ?",
                (unique_id,),
            ).fetchone()
        return dict(row) if row else None

    def voice_record_exists_by_path(self, file_path: str) -> bool:
        """检查指定路径的文件是否有对应的 DB 记录。"""

        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM a2_voice_info WHERE file_path = ? LIMIT 1",
                (file_path,),
            ).fetchone()
        return row is not None

    def list_voice_records(self) -> list[dict[str, Any]]:
        """列出全部语音记录，供同步任务全量扫描。"""

        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_voice_info ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

    def search_voice_records(
        self,
        *,
        unique_id: str | None,
        icao_code: str | None,
        band: str | None,
        start_time: str | None,
        end_time: str | None,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """按多个可选条件搜索语音记录。

        这是给集成接口使用的更通用查询：
        既可以按唯一 ID 精确找，也可以按机场、频段、时间范围组合过滤。
        """

        filters = ["valid_status = 'valid'"]
        params: list[Any] = []
        if unique_id:
            filters.append("unique_id = ?")
            params.append(unique_id)
        if icao_code:
            filters.append("icao_code = ?")
            params.append(icao_code)
        if band:
            filters.append("band = ?")
            params.append(band)
        if start_time and end_time:
            filters.append("start_at < ?")
            filters.append("end_at > ?")
            params.extend([end_time, start_time])
        elif start_time:
            filters.append("end_at > ?")
            params.append(start_time)
        elif end_time:
            filters.append("start_at < ?")
            params.append(end_time)

        where_sql = " AND ".join(filters)
        with get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(1) FROM a2_voice_info WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM a2_voice_info
                WHERE {where_sql}
                ORDER BY start_at ASC, unique_id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page_num - 1) * page_size]),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def update_voice_status(
        self,
        unique_id: str,
        *,
        valid_status: str,
        file_size: int | None = None,
        checksum: str | None = None,
    ) -> None:
        """更新语音记录的有效状态、文件大小和校验值。"""

        fields = ["valid_status = ?"]
        params: list[Any] = [valid_status]
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(file_size)
        if checksum is not None:
            fields.append("checksum = ?")
            params.append(checksum)
        params.append(unique_id)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE a2_voice_info SET {', '.join(fields)} WHERE unique_id = ?",
                tuple(params),
            )


class TaskRepository:
    """负责实时任务、下载任务和系统配置的数据库操作。"""

    def create_realtime_task(self, payload: RealtimeTaskCreate) -> int:
        """创建一条实时任务配置记录。"""

        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO a2_task_realtime_cfg (
                    task_name, server_addr, server_port, protocol, timeout, heart_beat, icao_code, band,
                    source_url, segment_seconds, stream_format, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    payload.task_name,
                    payload.server_addr,
                    payload.server_port,
                    payload.protocol,
                    payload.timeout,
                    payload.heart_beat,
                    payload.icao_code,
                    payload.band,
                    payload.source_url,
                    payload.segment_seconds,
                    payload.stream_format,
                ),
            )
            return int(cursor.lastrowid)

    def create_download_task(self, payload: DownloadTaskCreate) -> int:
        """创建一条历史下载任务配置记录。"""

        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO a2_task_download_cfg (
                    task_name, icao_code, band, start_time, end_time, speed_limit, exec_type, exec_time, status, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    payload.task_name,
                    payload.icao_code,
                    payload.band,
                    payload.start_time,
                    payload.end_time,
                    payload.speed_limit,
                    payload.exec_type,
                    payload.exec_time,
                    payload.priority,
                ),
            )
            return int(cursor.lastrowid)

    def list_realtime_tasks(self) -> list[dict[str, Any]]:
        """按任务 ID 倒序列出实时任务。"""

        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_task_realtime_cfg ORDER BY task_id DESC").fetchall()
        return [dict(row) for row in rows]

    def list_realtime_tasks_filtered(
        self,
        *,
        icao_code: str | None,
        band: str | None,
        status: int | None,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """按条件分页查询实时任务。"""

        filters = ["1 = 1"]
        params: list[Any] = []
        if icao_code:
            filters.append("icao_code = ?")
            params.append(icao_code)
        if band:
            filters.append("band = ?")
            params.append(band)
        if status is not None:
            filters.append("status = ?")
            params.append(status)

        where_sql = " AND ".join(filters)
        with get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(1) FROM a2_task_realtime_cfg WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM a2_task_realtime_cfg
                WHERE {where_sql}
                ORDER BY task_id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page_num - 1) * page_size]),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def get_realtime_task(self, task_id: int) -> dict[str, Any] | None:
        """获取单条实时任务配置。"""

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_task_realtime_cfg WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_download_tasks(self) -> list[dict[str, Any]]:
        """按任务 ID 倒序列出下载任务。"""

        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_task_download_cfg ORDER BY task_id DESC").fetchall()
        return [dict(row) for row in rows]

    def list_download_tasks_filtered(
        self,
        *,
        icao_code: str | None,
        band: str | None,
        status: int | None,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """按条件分页查询下载任务。"""

        filters = ["1 = 1"]
        params: list[Any] = []
        if icao_code:
            filters.append("icao_code = ?")
            params.append(icao_code)
        if band:
            filters.append("band = ?")
            params.append(band)
        if status is not None:
            filters.append("status = ?")
            params.append(status)

        where_sql = " AND ".join(filters)
        with get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(1) FROM a2_task_download_cfg WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM a2_task_download_cfg
                WHERE {where_sql}
                ORDER BY task_id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page_num - 1) * page_size]),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def get_download_task(self, task_id: int) -> dict[str, Any] | None:
        """获取单条下载任务配置。"""

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_task_download_cfg WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_download_progress(self, task_id: int, progress: float, resume_from: int, status: int) -> None:
        """更新下载任务的进度、续传偏移量和状态码。

        这里的 `resume_from` 表示已下载的字节数，后续如果再次下载，
        可以通过 HTTP Range 从这个位置继续请求。
        """

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE a2_task_download_cfg
                SET progress = ?, resume_from = ?, status = ?
                WHERE task_id = ?
                """,
                (progress, resume_from, status, task_id),
            )

    def update_download_task_time_range(self, task_id: int, start_time: str, end_time: str) -> None:
        """在元数据推断完成后回填下载任务时间范围。

        某些历史文件一开始只有 URL，没有明确时间范围，
        需要在解析文件名或探测音频时长后再把时间写回任务表。
        """

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE a2_task_download_cfg
                SET start_time = ?, end_time = ?
                WHERE task_id = ?
                """,
                (start_time, end_time, task_id),
            )

    def update_realtime_status(self, task_id: int, status: int) -> None:
        """更新实时任务当前运行状态。"""

        with get_conn() as conn:
            conn.execute(
                "UPDATE a2_task_realtime_cfg SET status = ? WHERE task_id = ?",
                (status, task_id),
            )

    def upsert_realtime_task(self, payload: IntegrationRealtimeTaskUpsertRequest) -> int:
        """供集成接口新增或更新实时任务。"""

        with get_conn() as conn:
            if payload.task_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM a2_task_realtime_cfg WHERE task_id = ?",
                    (payload.task_id,),
                ).fetchone()
                if exists:
                    conn.execute(
                        """
                        UPDATE a2_task_realtime_cfg
                        SET task_name = ?, server_addr = ?, server_port = ?, protocol = ?, timeout = ?,
                            heart_beat = ?, icao_code = ?, band = ?, source_url = ?, segment_seconds = ?,
                            stream_format = ?, status = ?
                        WHERE task_id = ?
                        """,
                        (
                            payload.task_name,
                            payload.server_addr,
                            payload.server_port,
                            payload.protocol,
                            payload.timeout,
                            payload.heart_beat,
                            payload.icao_code,
                            payload.band,
                            payload.source_url,
                            payload.segment_seconds,
                            payload.stream_format,
                            payload.status,
                            payload.task_id,
                        ),
                    )
                    return payload.task_id

            cursor = conn.execute(
                """
                INSERT INTO a2_task_realtime_cfg (
                    task_name, server_addr, server_port, protocol, timeout, heart_beat, icao_code, band,
                    source_url, segment_seconds, stream_format, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.task_name,
                    payload.server_addr,
                    payload.server_port,
                    payload.protocol,
                    payload.timeout,
                    payload.heart_beat,
                    payload.icao_code,
                    payload.band,
                    payload.source_url,
                    payload.segment_seconds,
                    payload.stream_format,
                    payload.status,
                ),
            )
            return int(cursor.lastrowid)

    def upsert_download_task(self, payload: IntegrationDownloadTaskUpsertRequest) -> int:
        """供集成接口新增或更新下载任务。"""

        with get_conn() as conn:
            if payload.task_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM a2_task_download_cfg WHERE task_id = ?",
                    (payload.task_id,),
                ).fetchone()
                if exists:
                    conn.execute(
                        """
                        UPDATE a2_task_download_cfg
                        SET task_name = ?, icao_code = ?, band = ?, start_time = ?, end_time = ?,
                            speed_limit = ?, exec_type = ?, exec_time = ?, status = ?, priority = ?
                        WHERE task_id = ?
                        """,
                        (
                            payload.task_name,
                            payload.icao_code,
                            payload.band,
                            payload.start_time,
                            payload.end_time,
                            payload.speed_limit,
                            payload.exec_type,
                            payload.exec_time,
                            payload.status,
                            payload.priority,
                            payload.task_id,
                        ),
                    )
                    return payload.task_id

            cursor = conn.execute(
                """
                INSERT INTO a2_task_download_cfg (
                    task_name, icao_code, band, start_time, end_time, speed_limit, exec_type, exec_time, status, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.task_name,
                    payload.icao_code,
                    payload.band,
                    payload.start_time,
                    payload.end_time,
                    payload.speed_limit,
                    payload.exec_type,
                    payload.exec_time,
                    payload.status,
                    payload.priority,
                ),
            )
            return int(cursor.lastrowid)

    def get_system_config(self) -> dict[str, Any]:
        """读取系统基础配置。"""

        with get_conn() as conn:
            row = conn.execute("SELECT * FROM a2_sys_base_cfg WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def update_system_config(self, payload: A2SystemConfigUpdateRequest) -> dict[str, Any]:
        """更新系统基础配置并返回最新结果。"""

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE a2_sys_base_cfg
                SET storage_root = ?, slice_rule = ?, max_download_task = ?, max_realtime_conn = ?,
                    api_timeout = ?, sync_interval = ?, update_time = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (
                    payload.storage_root,
                    payload.slice_rule,
                    payload.max_download_task,
                    payload.max_realtime_conn,
                    payload.api_timeout,
                    payload.sync_interval,
                ),
            )
            row = conn.execute("SELECT * FROM a2_sys_base_cfg WHERE id = 1").fetchone()
        return dict(row) if row else {}
