"""语音查询服务。

这一层负责把底层数据库查询结果转换成更适合接口返回的结构，
包括补充关联航迹、下载地址等衍生信息。
"""

from __future__ import annotations

from app.repositories import VoiceRepository
from app.schemas import IntegrationAudioQueryRequest, VoiceQueryRequest


class QueryService:
    def __init__(self, repository: VoiceRepository | None = None) -> None:
        """允许外部注入 Repository，便于测试替换。"""

        self.repository = repository or VoiceRepository()

    def query_voice(self, payload: VoiceQueryRequest) -> tuple[int, list[dict]]:
        """执行 A-2 主查询接口使用的时间范围检索。"""

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
        """执行面向集成系统的通用语音查询。"""

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

    def _enrich_rows(self, rows: list[dict]) -> list[dict]:
        """给原始查询结果补充航迹关联和下载地址。

        这样 API 层返回给前端或外部系统的结果就是“开箱即用”的，
        不需要调用方再自己二次拼接下载链接。
        """

        enriched: list[dict] = []
        for row in rows:
            # 根据机场和时间范围补查相关航迹，并把关系表补齐。
            track_ids = self.repository.find_tracks(row["icao_code"], row["start_at"], row["end_at"])
            for track_id in track_ids:
                self.repository.upsert_voice_track_rel(row["unique_id"], track_id)
            row["trackIds"] = track_ids
            row["downloadUrl"] = f"/api/a2/voice/file/{row['unique_id']}"
            enriched.append(row)
        return enriched
