"""语音查询服务。

负责把底层数据库查询结果包装成接口返回结构。
"""

from __future__ import annotations

from app.repositories import VoiceRepository
from app.schemas import IntegrationAudioQueryRequest, VoiceQueryRequest


class QueryService:
    def __init__(self, repository: VoiceRepository | None = None) -> None:
        self.repository = repository or VoiceRepository()

    def query_voice(self, payload: VoiceQueryRequest) -> tuple[int, list[dict]]:
        total, rows = self.repository.query_voice_records(
            start_time=payload.startTime,
            end_time=payload.endTime,
            icao_code=payload.icaoCode.upper() if payload.icaoCode else None,
            band=payload.band,
            page_num=payload.pageNum,
            page_size=payload.pageSize,
        )
        return total, self._enrich_rows(rows)

    def list_audio(self, payload: IntegrationAudioQueryRequest) -> tuple[int, list[dict]]:
        total, rows = self.repository.search_voice_records(
            unique_id=payload.unique_id,
            icao_code=payload.icao_code,
            band=payload.band,
            start_time=payload.start_time,
            end_time=payload.end_time,
            page_num=payload.page,
            page_size=payload.page_size,
        )
        return total, self._enrich_rows(rows)

    @staticmethod
    def _enrich_rows(rows: list[dict]) -> list[dict]:
        for row in rows:
            row["downloadUrl"] = f"/api/a2/voice/file/{row['unique_id']}"
        return rows
