from __future__ import annotations

from typing import Any

from app.db import get_conn
from app.schemas import DownloadTaskCreate, RealtimeTaskCreate, VoiceRecord


class VoiceRepository:
    def insert_voice_record(self, record: VoiceRecord) -> None:
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
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_voice_info WHERE unique_id = ?",
                (unique_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_voice_records(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_voice_info ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

    def update_voice_status(
        self,
        unique_id: str,
        *,
        valid_status: str,
        file_size: int | None = None,
        checksum: str | None = None,
    ) -> None:
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

    def upsert_voice_track_rel(self, unique_id: str, track_id: str) -> None:
        with get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM a2_voice_track_rel WHERE unique_id = ? AND track_id = ?",
                (unique_id, track_id),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO a2_voice_track_rel (unique_id, track_id) VALUES (?, ?)",
                    (unique_id, track_id),
                )

    def find_tracks(self, icao_code: str, start_time: str, end_time: str) -> list[str]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT track_id FROM adsb_tracks
                WHERE icao_code = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
                """,
                (icao_code, start_time, end_time),
            ).fetchall()
        return [row["track_id"] for row in rows]


class TaskRepository:
    def create_realtime_task(self, payload: RealtimeTaskCreate) -> int:
        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO a2_task_realtime_cfg (
                    task_name, server_addr, server_port, protocol, timeout, heart_beat, icao_code, band, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
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
                ),
            )
            return int(cursor.lastrowid)

    def create_download_task(self, payload: DownloadTaskCreate) -> int:
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
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_task_realtime_cfg ORDER BY task_id DESC").fetchall()
        return [dict(row) for row in rows]

    def get_realtime_task(self, task_id: int) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_task_realtime_cfg WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_download_tasks(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM a2_task_download_cfg ORDER BY task_id DESC").fetchall()
        return [dict(row) for row in rows]

    def get_download_task(self, task_id: int) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM a2_task_download_cfg WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_download_progress(self, task_id: int, progress: float, resume_from: int, status: int) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE a2_task_download_cfg
                SET progress = ?, resume_from = ?, status = ?
                WHERE task_id = ?
                """,
                (progress, resume_from, status, task_id),
            )

    def update_realtime_status(self, task_id: int, status: int) -> None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE a2_task_realtime_cfg SET status = ? WHERE task_id = ?",
                (status, task_id),
            )
